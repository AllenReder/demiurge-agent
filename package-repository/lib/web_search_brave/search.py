from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

import yaml


PROVIDER = "brave"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_search_config() -> dict[str, Any]:
    return _load_yaml_mapping(Path(__file__).with_name("config.yaml"))


def search_web(args: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return _error("query is required", query=query)
    api_key = _resolve_secret(config)
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_BRAVE_SEARCH_API_KEY")
        return _error(f"Brave Search API key is not configured; set {env_name} or config.api_key", query=query)

    try:
        params = _request_params(args, config, query=query)
    except ValueError as exc:
        return _error(str(exc), query=query)

    base_url = str(config.get("base_url") or "https://api.search.brave.com/res/v1/web/search")
    url = f"{base_url}?{urllib.parse.urlencode(params, doseq=True)}"
    timeout = _positive_int(config.get("timeout_seconds"), default=30, maximum=120)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "demiurge/0.3",
            "X-Subscription-Token": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", getattr(response, "code", 200)) or 200)
            body = response.read().decode(_charset(response), errors="replace")
    except urllib.error.HTTPError as exc:
        return _error(f"Brave Search failed: HTTP {exc.code} {exc.reason}", query=query, secrets=[api_key])
    except urllib.error.URLError as exc:
        return _error(f"Brave Search failed: {exc.reason}", query=query, secrets=[api_key])
    except OSError as exc:
        return _error(f"Brave Search failed: {exc}", query=query, secrets=[api_key])

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _error(f"Brave Search returned invalid JSON: {exc}", query=query)

    results = []
    raw_results = _mapping(payload.get("web")).get("results")
    if isinstance(raw_results, list):
        for index, item in enumerate(raw_results, start=1):
            if not isinstance(item, Mapping):
                continue
            url_value = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or item.get("snippet") or "").strip()
            if not url_value and not title and not description:
                continue
            results.append(
                {
                    "title": title,
                    "url": url_value,
                    "description": description,
                    "position": index,
                }
            )

    metadata = {
        "status": status,
        "requested_count": params.get("count"),
        "result_count": len(results),
    }
    query_info = _mapping(payload.get("query"))
    if query_info:
        metadata["query"] = {
            key: query_info.get(key)
            for key in ("original", "altered", "spellcheck_off")
            if query_info.get(key) is not None
        }
    return {
        "success": True,
        "provider": PROVIDER,
        "query": query,
        "data": {"web": results},
        "metadata": _drop_empty(metadata),
    }


def tool_json_result(result: Mapping[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def tool_display_output(result: Mapping[str, Any]) -> str:
    query = str(result.get("query") or "").strip()
    if not result.get("success"):
        error = str(result.get("error") or "unknown error")
        return f"Brave web_search failed: {error}"
    web = _list(_mapping(result.get("data")).get("web"))
    if not web:
        return f"Brave web_search: no results for {query!r}"
    lines = [f"Brave web_search: {len(web)} result(s) for {query!r}"]
    for item in web[:5]:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "(untitled)")
        url = str(item.get("url") or "")
        position = item.get("position") or len(lines)
        lines.append(f"{position}. {title} - {url}")
    return "\n".join(lines)


def _request_params(args: Mapping[str, Any], config: Mapping[str, Any], *, query: str) -> dict[str, Any]:
    count = _positive_int(args.get("count"), default=_positive_int(config.get("default_count"), default=5), maximum=20)
    params: dict[str, Any] = {"q": query, "count": count}
    for arg_key, param_key in (
        ("country", "country"),
        ("search_lang", "search_lang"),
        ("ui_lang", "ui_lang"),
        ("safesearch", "safesearch"),
    ):
        value = _optional_text(args.get(arg_key))
        if value:
            params[param_key] = value
    date_after = _optional_text(args.get("date_after"))
    date_before = _optional_text(args.get("date_before"))
    freshness = _optional_text(args.get("freshness"))
    if date_after or date_before:
        if not date_after or not date_before:
            raise ValueError("date_after and date_before must be provided together for Brave date-bounded search")
        if not DATE_RE.match(date_after) or not DATE_RE.match(date_before):
            raise ValueError("date_after and date_before must use YYYY-MM-DD")
        params["freshness"] = f"{date_after}to{date_before}"
    elif freshness:
        params["freshness"] = freshness
    return params


def _resolve_secret(config: Mapping[str, Any]) -> str:
    for value in (
        config.get("api_key"),
        os.getenv(str(config.get("api_key_env") or "DEMIURGE_BRAVE_SEARCH_API_KEY")),
        *[os.getenv(str(name)) for name in _list(config.get("fallback_envs"))],
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _error(message: str, *, query: str = "", secrets: list[str] | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "provider": PROVIDER,
        "query": query,
        "error": _sanitize(message, secrets or []),
        "data": {"web": []},
        "metadata": {},
    }


def _sanitize(text: str, secrets: list[str]) -> str:
    clean = text
    for secret in secrets:
        if secret:
            clean = clean.replace(secret, "<redacted>")
    return clean


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _positive_int(value: Any, *, default: int, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(1, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _drop_empty(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", {}, [])}


def _charset(response: Any) -> str:
    headers = getattr(response, "headers", None)
    get_content_charset = getattr(headers, "get_content_charset", None)
    if callable(get_content_charset):
        return get_content_charset() or "utf-8"
    return "utf-8"
