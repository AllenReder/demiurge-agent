from __future__ import annotations

from typing import Any

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore, PhasePipeline
from demiurge.providers import LLMMessage
from demiurge.runtime.context import ContextAssembler
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import SessionInteractionRouter
from demiurge.runtime.prompt_context import PromptBuildRequest, PromptContextRuntime, PromptDebugRequest
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.sdk import AgentInput, ContextContribution, TurnContext


def _core(tmp_path) -> LoadedCore:
    manifest = CoreManifest(agent=AgentInfo(id="assistant"))
    return LoadedCore(
        root=tmp_path / "core",
        manifest_path=tmp_path / "core" / "agent.yaml",
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul="SOUL",
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


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="current user"),
    )


class _Harness:
    def __init__(self, tmp_path, *, show_system_prompt: bool = False) -> None:
        self.store = RuntimeStore(tmp_path / "runtime.sqlite3")
        self.sessions = SessionRuntime(control_plane=RuntimeControlPlane(self.store))
        self.sessions.ensure_session("session_1", core_id="assistant", core_revision="rev_1")
        self.router = SessionInteractionRouter()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.show_system_prompt = show_system_prompt
        self.runtime = PromptContextRuntime(
            assembler=ContextAssembler(),
            sessions=self.sessions,
            interaction_router=self.router,
            session_id=lambda: "session_1",
            show_system_prompt=lambda: self.show_system_prompt,
            emit_event=self.emit_event,
        )

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}


def test_prompt_context_builds_messages_from_session_layers_and_emits_event(tmp_path):
    harness = _Harness(tmp_path)
    old = harness.sessions.append_message("session_1", role="user", content="old history", turn_id="turn_old")
    harness.sessions.append_message("session_1", role="user", content="current persisted", turn_id="turn_1")
    harness.sessions.write_bootstrap_context("session_1", "BOOT")
    harness.sessions.write_compaction_summary(
        "session_1",
        content="SUMMARY",
        turn_id="compact_1",
        compacted_until_message_id=old.id,
        compacted_count=1,
    )

    messages = harness.runtime.build_messages(
        PromptBuildRequest(
            core=_core(tmp_path),
            context=[ContextContribution(type="instruction", content="INJECTED", placement="system_context")],
            turn_messages=[LLMMessage(role="user", content="current user")],
            turn_id="turn_1",
            step_id="step_1",
        )
    )

    system = next(message for message in messages if message.role == "system")
    assert "SOUL" in system.content
    assert "BOOT" in system.content
    assert "SUMMARY" in system.content
    assert "INJECTED" in system.content
    assert all("old history" not in (message.content or "") for message in messages)
    assert all("current persisted" not in (message.content or "") for message in messages)
    assert messages[-1].content == "current user"
    assert harness.events[0][0] == "context.assembled"
    assert [layer["name"] for layer in harness.events[0][1]["layers"]] == [
        "core_soul",
        "bootstrap_context",
        "system_context",
        "compaction_summary",
        "current_turn",
    ]


@pytest.mark.asyncio
async def test_prompt_debug_disabled_does_not_emit_or_deliver(tmp_path):
    harness = _Harness(tmp_path, show_system_prompt=False)

    await harness.runtime.deliver_system_prompt_debug(
        PromptDebugRequest(
            messages=[LLMMessage(role="system", content="SYS")],
            turn=_turn(),
            step_id="step_1",
            interaction_metadata={"channel": "tui"},
        )
    )

    assert harness.events == []


@pytest.mark.asyncio
async def test_prompt_debug_unrouted_channel_records_unrouted_event(tmp_path):
    harness = _Harness(tmp_path, show_system_prompt=True)

    await harness.runtime.deliver_system_prompt_debug(
        PromptDebugRequest(
            messages=[LLMMessage(role="system", content="SYS")],
            turn=_turn(),
            step_id="step_1",
            interaction_metadata={"channel": "tui"},
        )
    )

    assert harness.events == [
        (
            "debug.system_prompt.unrouted",
            {
                "turn_id": "turn_1",
                "step_id": "step_1",
                "system_messages": 1,
                "total_chars": 3,
                "channel": "tui",
            },
        )
    ]
