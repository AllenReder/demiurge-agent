from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from openai import AsyncOpenAI


DEFAULT_MODELS = ("gpt-5.4", "minimax-m3", "deepseek-v4-flash")


@dataclass(slots=True)
class ProbeResult:
    api: str
    model: str
    scenario: str
    ok: bool
    status_code: int | None
    error_type: str | None
    error_message: str | None
    elapsed_ms: int
    output_preview: str | None = None


def _short(value: Any, limit: int = 260) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _status_code(exc: BaseException) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


def _chat_scenarios() -> dict[str, dict[str, Any]]:
    tool = {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo a short string.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    }
    return {
        "simple_user": {
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        },
        "consecutive_users": {
            "messages": [
                {"role": "user", "content": "First user message."},
                {"role": "user", "content": "Reply with exactly: ok"},
            ],
        },
        "unknown_role": {
            "messages": [{"role": "banana", "content": "Reply with exactly: ok"}],
        },
        "orphan_tool": {
            "messages": [
                {"role": "user", "content": "A tool result follows."},
                {"role": "tool", "tool_call_id": "call_orphan", "content": "orphan"},
                {"role": "user", "content": "Reply with exactly: ok"},
            ],
        },
        "valid_tool_replay": {
            "messages": [
                {"role": "user", "content": "Call echo."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_valid",
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{\"text\":\"ok\"}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_valid", "content": "{\"text\":\"ok\"}"},
                {"role": "user", "content": "Reply with exactly: ok"},
            ],
            "tools": [tool],
        },
        "bad_tool_arguments": {
            "messages": [
                {"role": "user", "content": "Call echo."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_bad_args",
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{bad json"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_bad_args", "content": "{\"text\":\"ok\"}"},
                {"role": "user", "content": "Reply with exactly: ok"},
            ],
            "tools": [tool],
        },
        "assistant_tool_without_result": {
            "messages": [
                {"role": "user", "content": "Call echo."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_missing_result",
                            "type": "function",
                            "function": {"name": "echo", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "user", "content": "Reply with exactly: ok"},
            ],
            "tools": [tool],
        },
        "empty_assistant": {
            "messages": [
                {"role": "user", "content": "A blank assistant message follows."},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "Reply with exactly: ok"},
            ],
        },
    }


def _responses_scenarios() -> dict[str, dict[str, Any]]:
    return {
        "simple_string": {"input": "Reply with exactly: ok"},
        "message_array": {"input": [{"role": "user", "content": "Reply with exactly: ok"}]},
        "consecutive_users": {
            "input": [
                {"role": "user", "content": "First user message."},
                {"role": "user", "content": "Reply with exactly: ok"},
            ]
        },
        "unknown_role": {"input": [{"role": "banana", "content": "Reply with exactly: ok"}]},
        "orphan_function_output": {
            "input": [
                {"role": "user", "content": "A function output follows."},
                {"type": "function_call_output", "call_id": "call_orphan", "output": "{\"text\":\"ok\"}"},
                {"role": "user", "content": "Reply with exactly: ok"},
            ]
        },
        "empty_assistant": {
            "input": [
                {"role": "user", "content": "A blank assistant message follows."},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "Reply with exactly: ok"},
            ]
        },
    }


async def _run_call(
    api: str,
    client: AsyncOpenAI,
    model: str,
    scenario: str,
    payload: dict[str, Any],
) -> ProbeResult:
    started = time.monotonic()
    try:
        if api == "chat":
            response = await client.chat.completions.create(
                model=model,
                messages=payload["messages"],
                tools=payload.get("tools"),
                temperature=0,
            )
            message = response.choices[0].message
            preview = message.content or json.dumps(
                [call.model_dump() for call in message.tool_calls or []], ensure_ascii=False
            )
        elif api == "responses":
            response = await client.responses.create(
                model=model,
                input=payload["input"],
                temperature=0,
            )
            preview = getattr(response, "output_text", "") or response.model_dump_json()[:220]
        else:
            raise ValueError(f"unknown api: {api}")
        return ProbeResult(
            api=api,
            model=model,
            scenario=scenario,
            ok=True,
            status_code=None,
            error_type=None,
            error_message=None,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            output_preview=_short(preview),
        )
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic probe.
        return ProbeResult(
            api=api,
            model=model,
            scenario=scenario,
            ok=False,
            status_code=_status_code(exc),
            error_type=type(exc).__name__,
            error_message=_short(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


async def _run(args: argparse.Namespace) -> list[ProbeResult]:
    api_key = args.api_key or os.environ.get("PROBE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = args.base_url or os.environ.get("PROBE_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not api_key:
        raise SystemExit("Set PROBE_API_KEY or pass --api-key.")
    if not base_url:
        raise SystemExit("Set PROBE_BASE_URL or pass --base-url.")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=args.timeout)
    models = args.models or list(DEFAULT_MODELS)
    apis = ["chat", "responses"] if args.api == "both" else [args.api]
    scenario_getters: dict[str, Callable[[], dict[str, dict[str, Any]]]] = {
        "chat": _chat_scenarios,
        "responses": _responses_scenarios,
    }
    results: list[ProbeResult] = []
    for api in apis:
        scenarios = scenario_getters[api]()
        selected = args.scenarios or list(scenarios)
        for model in models:
            for scenario in selected:
                if scenario not in scenarios:
                    raise SystemExit(f"Unknown {api} scenario: {scenario}")
                result = await _run_call(api, client, model, scenario, scenarios[scenario])
                results.append(result)
                status = "OK" if result.ok else f"ERR {result.status_code or '-'}"
                print(f"{api:9} {model:20} {scenario:29} {status:8} {result.elapsed_ms:5}ms")
                if result.error_message:
                    print(f"  {result.error_type}: {result.error_message}")
                elif result.output_preview and args.verbose:
                    print(f"  output: {result.output_preview}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe provider replay/message-shape compatibility.")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--api", choices=["chat", "responses", "both"], default="both")
    parser.add_argument("--model", dest="models", action="append", help="Model to test; repeatable.")
    parser.add_argument("--scenario", dest="scenarios", action="append", help="Scenario to test; repeatable.")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON results after the table.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    results = asyncio.run(_run(args))
    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
