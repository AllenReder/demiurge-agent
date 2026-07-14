from __future__ import annotations

from typing import Any

import pytest

from baseline_support import BaselineContractFailure
from demiurge.core import AgentInfo, CoreManifest, LoadedCore, PhasePipeline
from demiurge.providers import LLMMessage, LLMResponse
from demiurge.runtime.context import ContextAssembler
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.session_compaction import SessionCompactionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.storage import SessionMessage


pytestmark = pytest.mark.stress


def _core(tmp_path, *, soul: str = "SOUL") -> LoadedCore:
    manifest = CoreManifest(agent=AgentInfo(id="assistant"))
    return LoadedCore(
        root=tmp_path / "core",
        manifest_path=tmp_path / "core" / "agent.yaml",
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul=soul,
        bootstrap_slots=[],
        bootstrap_pipeline=PhasePipeline(),
        bootstrap_enabled=False,
        input_slots=[],
        output_slots=[],
        input_pipeline=PhasePipeline(),
        output_pipeline=PhasePipeline(),
        tool_slots=[],
        skills=[],
        schedules=[],
        mcp_servers=[],
    )


def _history(total_chars: int) -> list[SessionMessage]:
    message_count = 8
    chars_per_message = total_chars // message_count
    return [
        SessionMessage(
            id=f"message_{index}",
            session_id="session_context",
            turn_id=f"turn_{index // 2}",
            role="user" if index % 2 == 0 else "assistant",
            content=("h" * chars_per_message),
            created_at=f"2026-01-01T00:00:{index:02d}Z",
        )
        for index in range(message_count)
    ]


@pytest.mark.parametrize(
    "history_chars",
    [
        6_000,
        pytest.param(
            12_000,
            marks=pytest.mark.xfail(
                strict=True,
                raises=BaselineContractFailure,
                reason="CTX-01: assembled context is not bounded before provider IO",
            ),
        ),
    ],
)
def test_context_near_and_over_window_capacity_baseline(
    tmp_path,
    baseline_recorder,
    history_chars,
):
    window_proxy_chars = 8_000
    current_input = "current-input-sentinel"
    assembler = ContextAssembler()

    with baseline_recorder.measure(
        "context_assembly_near_and_over_window",
        finding="CTX-01",
        scale={
            "history_chars": history_chars,
            "window_proxy_chars": window_proxy_chars,
        },
    ) as sample:
        assembled = assembler.assemble(
            core=_core(tmp_path),
            context=[],
            session_history=_history(history_chars),
            current_turn_messages=[LLMMessage(role="user", content=current_input)],
        )
        assembled_chars = sum(len(message.content or "") for message in assembled.messages)
        sample.observations.update(
            {
                "assembled_messages": len(assembled.messages),
                "assembled_chars": assembled_chars,
                "window_proxy_exceeded": assembled_chars > window_proxy_chars,
                "current_input_preserved": any(
                    message.role == "user" and message.content == current_input
                    for message in assembled.messages
                ),
                "layers": assembled.layer_summaries(),
            }
        )
        assert sample.observations["current_input_preserved"] is True
        sample.require(
            assembled_chars <= window_proxy_chars,
            "assembled context must not exceed the normalized input window",
        )


class RecordingCompactionProvider:
    def __init__(self) -> None:
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return LLMResponse(content="bounded summary placeholder")


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="CTX-01: manual compaction sends the complete over-window transcript",
)
async def test_manual_compaction_input_capacity_baseline(tmp_path, baseline_recorder):
    window_proxy_chars = 4_096
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    sessions.create_session(
        session_id="session_context",
        core_id="assistant",
        core_revision="rev_context",
    )
    for index in range(12):
        sessions.append_message(
            "session_context",
            role="user" if index % 2 == 0 else "assistant",
            content="c" * 1_024,
            turn_id=f"turn_{index // 2}",
        )
    provider = RecordingCompactionProvider()
    events: list[tuple[str, dict[str, Any]]] = []
    runtime = SessionCompactionRuntime(
        sessions=sessions,
        session_id=lambda: "session_context",
        load_core=lambda: _async_value(_core(tmp_path)),
        resolve_model_name=lambda core: "fake/context-baseline",
        complete_provider=provider.complete,
        emit_event=lambda event_type, **payload: (
            events.append((event_type, dict(payload))) or {"type": event_type, **payload}
        ),
        refresh_history=lambda: None,
    )

    with baseline_recorder.measure(
        "manual_compaction_input",
        finding="CTX-01",
        scale={"messages": 12, "message_chars": 1_024, "window_proxy_chars": window_proxy_chars},
    ) as sample:
        result = await runtime.compact(protect_last_n=0)
        request_chars = sum(
            len(message.content or "")
            for request in provider.requests
            for message in request.messages
        )
        sample.observations.update(
            {
                "provider_calls": len(provider.requests),
                "provider_request_chars": request_chars,
                "window_proxy_exceeded": request_chars > window_proxy_chars,
                "result_error": result.error,
                "result_skipped": result.skipped,
                "events": [event_type for event_type, _ in events],
            }
        )
        assert len(provider.requests) == 1
        assert result.error is None
        sample.require(
            request_chars <= window_proxy_chars,
            "manual compaction provider input must fit the normalized compaction window",
        )


async def _async_value(value):
    return value
