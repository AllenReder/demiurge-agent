from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from demiurge.app import create_app
from demiurge.ui_gateway.bridge import TuiInteractionBridge
from demiurge.ui_gateway.protocol import NdjsonRpcEndpoint
from demiurge.util import default_home


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m demiurge.ui_gateway.entry")
    parser.add_argument("--config-json", default=None, help="JSON config produced by the demiurge TUI launcher")
    return parser


def main(argv: list[str] | None = None) -> None:
    asyncio.run(async_main(argv))


async def async_main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = _load_config(args.config_json)
    endpoint = NdjsonRpcEndpoint()

    async def emit(event: str, payload: dict[str, Any]) -> None:
        await endpoint.write_event(event, payload)

    try:
        app = create_app(
            home=Path(config.get("home") or default_home()),
            core_id=str(config["core"]) if config.get("core") else None,
            agents_root=Path(config["agents_root"]) if config.get("agents_root") else None,
            provider_name=str(config.get("provider") or "auto"),
            model=config.get("model"),
            base_url=config.get("base_url"),
            api_key=config.get("api_key"),
            fake_script=Path(config["fake_script"]) if config.get("fake_script") else None,
            workspace=Path(config["workspace"]) if config.get("workspace") else None,
            tool_display=config.get("tool_display"),
            session_id=config.get("resume") or config.get("session"),
            resume_required=bool(config.get("resume")),
        )
        bridge = TuiInteractionBridge(app, emit=emit, tool_display=config.get("tool_display"), busy_mode=config.get("busy_mode"))
        await bridge.initialize()
    except Exception as exc:
        await endpoint.write_event("interaction.error", {"message": str(exc), "source": "gateway_startup"})
        return

    try:
        async for request in endpoint.iter_requests():
            message_id = request.get("id")
            method = str(request.get("method") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            try:
                result = await _dispatch(bridge, method, params)
            except Exception as exc:
                print(f"[demiurge.ui_gateway] {method} failed: {exc}", file=sys.stderr)
                await endpoint.write_error(message_id, str(exc)) if message_id is not None else await endpoint.write_event(
                    "interaction.error", {"message": str(exc), "source": "gateway_dispatch", "method": method}
                )
                continue
            if message_id is not None:
                await endpoint.write_result(message_id, result)
            if bridge.should_exit:
                return
    finally:
        await app.close()


async def _dispatch(bridge: TuiInteractionBridge, method: str, params: dict[str, Any]) -> Any:
    if method == "interaction.initialize":
        return await bridge.initialize()
    if method == "interaction.submit":
        return await bridge.submit(str(params.get("text") or ""))
    if method == "interaction.reply_prompt":
        return await bridge.reply_prompt(str(params.get("prompt_id") or ""), str(params.get("answer") or ""))
    if method == "interaction.reply_approval":
        return await bridge.reply_approval(str(params.get("approval_id") or ""), str(params.get("decision") or ""))
    if method == "channel.command":
        return await bridge.command(str(params.get("text") or ""))
    if method == "channel.interrupt":
        await bridge.interrupt_current_turn(reason=str(params.get("reason") or "channel.interrupt"))
        return {"interrupted": True}
    if method == "channel.shutdown":
        await bridge.shutdown()
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
