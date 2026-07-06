from __future__ import annotations

from typing import Any

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore, PhasePipeline
from demiurge.providers import LLMResponse
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.session_compaction import SUMMARY_END_MARKER, SUMMARY_PREFIX, SessionCompactionRuntime
from demiurge.runtime.store import RuntimeStore


def _core(tmp_path) -> LoadedCore:
    manifest = CoreManifest(agent=AgentInfo(id="assistant"))
    return LoadedCore(
        root=tmp_path / "core",
        manifest_path=tmp_path / "core" / "agent.yaml",
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul="",
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


class _Harness:
    def __init__(self, tmp_path, *, response: str = "summary body") -> None:
        self.core = _core(tmp_path)
        self.sessions = SessionRuntime(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
        self.sessions.ensure_session("session_1", core_id="assistant", core_revision="rev_1")
        self.response = response
        self.requests = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.refreshes = 0
        self.runtime = SessionCompactionRuntime(
            sessions=self.sessions,
            session_id=lambda: "session_1",
            load_core=self.load_core,
            resolve_model_name=lambda core: "fake/model",
            complete_provider=self.complete_provider,
            emit_event=self.emit_event,
            refresh_history=self.refresh_history,
        )

    async def load_core(self):
        return self.core

    async def complete_provider(self, request):
        self.requests.append(request)
        return LLMResponse(content=self.response)

    def emit_event(self, event_type: str, **payload: Any):
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}

    def refresh_history(self):
        self.refreshes += 1


@pytest.mark.asyncio
async def test_session_compaction_skips_when_not_enough_turns(tmp_path):
    harness = _Harness(tmp_path)
    harness.sessions.append_message("session_1", role="user", content="hello", turn_id="turn_1")

    result = await harness.runtime.compact(protect_last_n=6)

    assert result.skipped is True
    assert result.summary == "not enough history to compact"
    assert harness.requests == []
    assert [event[0] for event in harness.events] == [
        "session.compaction.started",
        "session.compaction.completed",
    ]
    assert harness.events[-1][1]["skipped"] is True


@pytest.mark.asyncio
async def test_session_compaction_persists_summary_and_refreshes_history(tmp_path):
    harness = _Harness(tmp_path, response="keep the decision")
    harness.sessions.append_message("session_1", role="user", content="first", turn_id="turn_1")
    harness.sessions.append_message(
        "session_1",
        role="assistant",
        content="used tool",
        turn_id="turn_1",
        metadata={"step_id": "step_1", "tool_calls": [{"id": "call_1", "name": "lookup", "arguments": {"q": "x"}}]},
    )
    harness.sessions.append_message("session_1", role="user", content="second", turn_id="turn_2")
    harness.sessions.append_message("session_1", role="user", content="protected", turn_id="turn_3")

    result = await harness.runtime.compact(focus="preserve decisions", protect_last_n=1)

    assert result.skipped is False
    assert result.compacted_count == 3
    assert result.summary.startswith(SUMMARY_PREFIX)
    assert result.summary.endswith(SUMMARY_END_MARKER)
    assert "keep the decision" in result.summary
    assert harness.refreshes == 1
    summary = harness.sessions.latest_compaction_summary("session_1")
    assert summary is not None
    assert summary.id == result.summary_message_id
    assert summary.content == result.summary
    request = harness.requests[0]
    assert request.model == "fake/model"
    assert request.metadata["kind"] == "session_compaction"
    assert "Focus: preserve decisions" in request.messages[1].content
    assert "TOOL_CALLS" in request.messages[1].content
    assert [event[0] for event in harness.events] == [
        "session.compaction.started",
        "session.compaction.completed",
    ]


@pytest.mark.asyncio
async def test_session_compaction_empty_provider_summary_returns_error(tmp_path):
    harness = _Harness(tmp_path, response="")
    harness.sessions.append_message("session_1", role="user", content="hello", turn_id="turn_1")

    result = await harness.runtime.compact(protect_last_n=0)

    assert result.error == "provider returned an empty compaction summary"
    assert result.summary_message_id is None
    assert harness.refreshes == 0
    assert [event[0] for event in harness.events] == [
        "session.compaction.started",
        "session.compaction.failed",
    ]
