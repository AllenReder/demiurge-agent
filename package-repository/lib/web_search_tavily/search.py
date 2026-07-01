from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.request

import yaml


PROVIDER = "tavily"


def load_search_config() -> dict[str, Any]:
    return _load_yaml_mapping(Path(__file__).with_name("config.yaml"))


def search_web(args: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return _error("query is required", query=query)
    api_key = _resolve_secret(config)
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_TAVILY_API_KEY")
        return _error(f"Tavily API key is not configured; set {env_name} or config.api_key", query=query)

    payload = _request_payload(args, config, query=query)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = str(config.get("base_url") or "https://api.tavily.com/search")
    timeout = _positive_int(config.get("timeout_seconds"), default=30, maximum=120)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "demiurge/0.3",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", getattr(response, "code", 200)) or 200)
            response_body = response.read().decode(_charset(response), errors="replace")
    except urllib.error.HTTPError as exc:
        return _error(f"Tavily search failed: HTTP {exc.code} {exc.reason}", query=query, secrets=[api_key])
    except urllib.error.URLError as exc:
        return _error(f"Tavily search failed: {exc.reason}", query=query, secrets=[api_key])
    except OSError as exc:
        return _error(f"Tavily search failed: {exc}", query=query, secrets=[api_key])

    try:
        response_payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        return _error(f"Tavily search returned invalid JSON: {exc}", query=query)

    results = []
    raw_results = response_payload.get("results")
    if isinstance(raw_results, list):
        for index, item in enumerate(raw_results, start=1):
            if not isinstance(item, Mapping):
                continue
            url_value = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            description = str(item.get("content") or item.get("description") or "").strip()
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

    output: dict[str, Any] = {
        "success": True,
        "provider": PROVIDER,
        "query": query,
        "data": {"web": results},
        "metadata": _drop_empty(
            {
                "status": status,
                "requested_count": payload.get("max_results"),
                "result_count": len(results),
                "response_time": response_payload.get("response_time"),
                "request_id": response_payload.get("request_id"),
            }
        ),
    }
    answer = response_payload.get("answer")
    if isinstance(answer, str) and answer.strip():
        output["answer"] = answer.strip()
    return output


def tool_json_result(result: Mapping[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def tool_display_output(result: Mapping[str, Any]) -> str:
    query = str(result.get("query") or "").strip()
    if not result.get("success"):
        error = str(result.get("error") or "unknown error")
        return f"Tavily web_search failed: {error}"
    web = _list(_mapping(result.get("data")).get("web"))
    lines = [f"Tavily web_search: {len(web)} result(s) for {query!r}"]
    answer = str(result.get("answer") or "").strip()
    if answer:
        lines.append(f"answer: {answer[:240]}")
    for item in web[:5]:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "(untitled)")
        url = str(item.get("url") or "")
        position = item.get("position") or len(lines)
        lines.append(f"{position}. {title} - {url}")
    return "\n".join(lines)


def _request_payload(args: Mapping[str, Any], config: Mapping[str, Any], *, query: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": query,
        "max_results": _positive_int(
            args.get("max_results"),
            default=_positive_int(config.get("default_max_results"), default=5),
            maximum=20,
        ),
    }
    for key in ("search_depth", "topic", "time_range", "start_date", "end_date", "country"):
        value = _optional_text(args.get(key))
        if value:
            payload[key] = value
    for key in ("include_domains", "exclude_domains"):
        values = _string_list(args.get(key))
        if values:
            payload[key] = values
    if "include_answer" in args and args.get("include_answer") is not None:
        payload["include_answer"] = _include_answer_value(args.get("include_answer"))
    return payload


def _include_answer_value(value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return text


def _resolve_secret(config: Mapping[str, Any]) -> str:
    for value in (
        config.get("api_key"),
        os.getenv(str(config.get("api_key_env") or "DEMIURGE_TAVILY_API_KEY")),
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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
