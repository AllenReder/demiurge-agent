from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any

from demiurge.core import McpServerDefinition


_SENSITIVE_ARGUMENT_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)


def mcp_connect_security_summary(
    server: McpServerDefinition,
) -> dict[str, Any]:
    manifest = server.manifest
    summary: dict[str, Any] = {
        "server_id": server.server_id,
        "path": server.relative_path,
        "enabled": manifest.enabled,
        "transport": manifest.transport,
        "risk": manifest.risk,
        "approval_policy": manifest.approval_policy,
        "effective_connect_approval_policy": _stricter_policy(
            "prompt",
            manifest.approval_policy,
        ),
        "call_capability": server.capability,
        "connect_capability": f"mcp.connect:{server.server_id}",
    }
    if manifest.transport == "stdio":
        summary.update(
            {
                "command": manifest.command,
                "args": safe_mcp_launch_args(manifest.args),
                "cwd": manifest.cwd or ".",
                "env_names": sorted(manifest.env),
            }
        )
    else:
        summary.update(
            {
                "url": safe_mcp_url_summary(manifest.url),
                "header_names": sorted(manifest.headers),
            }
        )
    return summary


def mcp_server_identity_payload(
    server: McpServerDefinition,
) -> dict[str, Any]:
    return {
        "server_id": server.server_id,
        "relative_path": server.relative_path,
        "raw_manifest": server.raw_manifest,
    }


def mcp_server_fingerprint(server: McpServerDefinition) -> str:
    text = json.dumps(
        mcp_server_identity_payload(server),
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_mcp_launch_args(args: list[str]) -> list[str]:
    summarized: list[str] = []
    index = 0
    while index < len(args):
        argument = str(args[index])
        option, separator, value = argument.partition("=")
        normalized = option.lstrip("-").lower().replace("-", "_")
        sensitive = any(
            part in normalized
            for part in _SENSITIVE_ARGUMENT_KEY_PARTS
        )
        if option.startswith("-") and sensitive:
            summarized.append(f"{option}=<redacted>")
            if not separator and index + 1 < len(args):
                index += 1
        elif option.startswith("-") and separator:
            summarized.append(
                f"{option}={opaque_mcp_value_summary(value)}"
            )
        elif option.startswith("-"):
            summarized.append(option)
        else:
            summarized.append(opaque_mcp_value_summary(argument))
        index += 1
    return summarized


def opaque_mcp_value_summary(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<value sha256:{digest} length:{len(value)}>"


def safe_mcp_url_summary(url: str | None) -> str | None:
    if url is None:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        query_names = sorted(
            {
                key
                for key, _value in urllib.parse.parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                )
            }
        )
        query = "&".join(
            f"{urllib.parse.quote(name, safe='')}=<redacted>"
            for name in query_names
        )
        safe_path = "/".join(
            opaque_mcp_value_summary(urllib.parse.unquote(segment))
            if segment
            else ""
            for segment in parsed.path.split("/")
        )
        return urllib.parse.urlunsplit(
            (parsed.scheme, netloc, safe_path, query, "")
        )
    except (TypeError, ValueError):
        return opaque_mcp_value_summary(url)


def _stricter_policy(left: str, right: str) -> str:
    order = {"auto": 0, "prompt": 1, "deny": 2}
    return left if order[left] >= order[right] else right
