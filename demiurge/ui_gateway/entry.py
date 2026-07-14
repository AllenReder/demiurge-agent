from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from demiurge.app import create_app
from demiurge.security.redaction import (
    RedactionView,
    SecretValue,
    redact_exception_message,
)
from demiurge.ui_gateway.bridge import OperatorGatewayRuntime
from demiurge.ui_gateway.protocol import NdjsonRpcEndpoint, TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION
from demiurge.util import default_home


LONG_OPERATOR_COMMANDS = frozenset({"/doctor", "/packages", "/evolve", "/rollback", "/compact"})


class TuiIdentityMismatch(ValueError):
    pass


class TuiStartupError(RuntimeError):
    pass


def _safe_exception_message(
    exc: BaseException,
    *,
    gateway: OperatorGatewayRuntime | None = None,
    app: Any | None = None,
) -> str:
    owner = app or getattr(gateway, "app", None)
    provider = getattr(getattr(owner, "runner", None), "provider", None)
    api_key = getattr(provider, "api_key", None)
    secrets = (
        (
            SecretValue(
                value=api_key,
                name="API_KEY",
                source="provider.api_key",
            ),
        )
        if isinstance(api_key, str) and api_key
        else ()
    )
    return redact_exception_message(
        exc,
        view=RedactionView.OPERATOR,
        secrets=secrets,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m demiurge.ui_gateway.entry")
    parser.add_argument("--config-json", default=None, help="JSON config produced by the demiurge TUI launcher")
    return parser


def main(argv: list[str] | None = None) -> None:
    exit_code = asyncio.run(async_main(argv))
    if exit_code:
        raise SystemExit(exit_code)


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    endpoint = NdjsonRpcEndpoint()

    try:
        config = _load_config(args.config_json)
    except Exception:
        await endpoint.write_event(
            "operator.error",
            {
                "code": "config_error",
                "message": "gateway configuration could not be loaded",
                "source": "gateway_config",
            },
        )
        return 2

    async def emit(event: str, payload: dict[str, Any]) -> None:
        await endpoint.write_event(event, payload)

    app = None
    try:
        app = create_app(
            home=Path(config.get("home") or default_home()),
            core_id=str(config["core"]) if config.get("core") else None,
            agents_root=Path(config["agents_root"]) if config.get("agents_root") else None,
            provider_name=str(config.get("provider") or "auto"),
            model=config.get("model"),
            fake_script=Path(config["fake_script"]) if config.get("fake_script") else None,
            workspace=Path(config["workspace"]) if config.get("workspace") else None,
            workspace_fallback=Path(config["workspace_fallback"]) if config.get("workspace_fallback") else None,
            tool_display=config.get("tool_display"),
            timezone=config.get("timezone"),
            session_id=config.get("resume") or config.get("session"),
            resume_required=bool(config.get("resume")),
        )
        gateway = OperatorGatewayRuntime(app, emit=emit, tool_display=config.get("tool_display"), busy_mode=config.get("busy_mode"))
    except Exception as exc:
        await endpoint.write_event(
            "operator.error",
            {
                "message": _safe_exception_message(exc, app=app),
                "source": "gateway_startup",
            },
        )
        if app is not None:
            await app.close()
        return 1

    pending_handlers: set[asyncio.Task[None]] = set()
    try:
        async for request in endpoint.iter_requests():
            message_id = request.get("id")
            method = str(request.get("method") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            if getattr(gateway, "_tui_identity_verified", False) and _is_long_operator_request(method, params):
                task = asyncio.create_task(_handle_request(endpoint, gateway, message_id, method, params))
                pending_handlers.add(task)
                task.add_done_callback(pending_handlers.discard)
                continue
            try:
                await _handle_request(endpoint, gateway, message_id, method, params)
            except TuiIdentityMismatch:
                return 2
            except TuiStartupError:
                return 1
            if gateway.should_exit:
                return 0
        return 1
    finally:
        for task in pending_handlers:
            task.cancel()
        if pending_handlers:
            await asyncio.gather(*pending_handlers, return_exceptions=True)
        await app.close()


async def _handle_request(
    endpoint: NdjsonRpcEndpoint,
    gateway: OperatorGatewayRuntime,
    message_id: object,
    method: str,
    params: dict[str, Any],
) -> None:
    try:
        result = await _dispatch(gateway, method, params)
    except TuiIdentityMismatch as exc:
        safe_error = _safe_exception_message(exc, gateway=gateway)
        print(f"[demiurge.ui_gateway] {method} failed: {safe_error}", file=sys.stderr)
        if message_id is not None:
            await endpoint.write_error(message_id, safe_error, code="protocol_mismatch")
        else:
            await endpoint.write_event(
                "operator.error",
                {
                    "message": safe_error,
                    "source": "gateway_protocol",
                    "method": method,
                    "code": "protocol_mismatch",
                },
            )
        raise
    except Exception as exc:
        safe_error = _safe_exception_message(exc, gateway=gateway)
        print(f"[demiurge.ui_gateway] {method} failed: {safe_error}", file=sys.stderr)
        if method == "operator.initialize" and not getattr(gateway, "_tui_identity_verified", False):
            await endpoint.write_event(
                "operator.error",
                {
                    "message": safe_error,
                    "method": method,
                    "source": "gateway_startup",
                },
            )
            raise TuiStartupError(safe_error) from exc
        if message_id is not None:
            await endpoint.write_error(message_id, safe_error)
        else:
            payload = {"message": safe_error, "source": "gateway_dispatch", "method": method}
            await endpoint.write_event("operator.error", payload)
        return
    if message_id is not None:
        await endpoint.write_result(message_id, result)


def _is_long_operator_request(method: str, params: dict[str, Any]) -> bool:
    if method != "operator.command":
        return False
    text = str(params.get("text") or "").strip()
    return any(text == command or text.startswith(f"{command} ") for command in LONG_OPERATOR_COMMANDS)


async def _dispatch(gateway: OperatorGatewayRuntime, method: str, params: dict[str, Any]) -> Any:
    if method == "operator.initialize":
        protocol_version = params.get("protocol_version")
        if protocol_version != TUI_PROTOCOL_VERSION:
            raise TuiIdentityMismatch(
                f"TUI protocol mismatch: expected {TUI_PROTOCOL_VERSION}, got {protocol_version!r}"
            )
        build_stamp = params.get("build_stamp")
        if build_stamp != TUI_BUILD_STAMP:
            raise TuiIdentityMismatch(
                f"TUI build mismatch: expected {TUI_BUILD_STAMP!r}, got {build_stamp!r}"
            )
        result = await gateway.initialize()
        setattr(gateway, "_tui_identity_verified", True)
        return {
            **result,
            "protocol_version": TUI_PROTOCOL_VERSION,
            "build_stamp": TUI_BUILD_STAMP,
        }
    if not getattr(gateway, "_tui_identity_verified", False):
        raise TuiIdentityMismatch(
            f"TUI identity handshake required before method: {method or '<empty>'}"
        )
    if method == "operator.submit":
        return await gateway.submit(str(params.get("text") or ""))
    if method == "operator.reply_prompt":
        return await gateway.reply_prompt(str(params.get("prompt_id") or ""), str(params.get("answer") or ""))
    if method == "operator.reply_approval":
        return await gateway.reply_approval(str(params.get("approval_id") or ""), str(params.get("decision") or ""))
    if method == "operator.command":
        return await gateway.command(str(params.get("text") or ""))
    if method == "operator.interrupt":
        await gateway.interrupt_current_turn(reason=str(params.get("reason") or "operator.interrupt"))
        return {"interrupted": True}
    if method == "operator.shutdown":
        await gateway.shutdown()
        return {"shutdown": True}
    raise ValueError(f"unknown method: {method}")


def _load_config(config_json: str | None) -> dict[str, Any]:
    raw = config_json or os.environ.get("DEMIURGE_TUI_GATEWAY_CONFIG") or "{}"
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("gateway config must be a JSON object")
    return data


if __name__ == "__main__":
    main()
