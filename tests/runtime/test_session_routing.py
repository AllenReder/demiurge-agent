from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.session_routing import SessionCoreBinding, SessionRoutingRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.runtime_timezone import RuntimeTimezone


def _binding() -> SessionCoreBinding:
    return SessionCoreBinding(
        core_id="assistant",
        core_revision="rev_1",
        provider="fake",
        model="fake/model",
        workspace="/workspace",
    )


def _routing(tmp_path, *, initial_session_id: str = "session_current"):
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    state = {"session_id": initial_session_id}
    activations: list[str] = []
    events: list[dict] = []

    def activate_session(session_id: str) -> None:
        state["session_id"] = session_id
        activations.append(session_id)

    def emit_event(event_type: str, **payload):
        event = {"type": event_type, **payload}
        events.append(event)
        return event

    runtime = SessionRoutingRuntime(
        sessions=sessions,
        session_id=lambda: state["session_id"],
        activate_session=activate_session,
        runtime_timezone=RuntimeTimezone(ZoneInfo("UTC"), "UTC", "test", True),
        emit_event=emit_event,
    )
    return runtime, sessions, state, activations, events


def test_metadata_for_interaction_adds_route_and_timezone(tmp_path):
    runtime, _, _, _, _ = _routing(tmp_path)

    metadata = runtime.metadata_for(
        InteractionInbound(
            channel="telegram",
            text="hello",
            source="chat_1",
            reply_to="msg_1",
            conversation_key="telegram:chat_1",
            metadata={"native_message_id": "native_1"},
        )
    )

    assert metadata["channel"] == "telegram"
    assert metadata["source"] == "chat_1"
    assert metadata["reply_to"] == "msg_1"
    assert metadata["conversation_key"] == "telegram:chat_1"
    assert metadata["native_message_id"] == "native_1"
    assert metadata["runtime_timezone"] == "UTC"
    assert metadata["runtime_timezone_source"] == "test"
    assert metadata["runtime_timezone_explicit"] is True


def test_ensure_current_creates_and_activates_session(tmp_path):
    runtime, sessions, state, activations, events = _routing(tmp_path)

    record = runtime.ensure_current(_binding())

    assert state["session_id"] == "session_current"
    assert activations == ["session_current"]
    assert sessions.get_session(record.session_id).core_revision == "rev_1"
    assert events == [{"type": "session.created", "core_id": "assistant", "core_revision": "rev_1"}]


def test_resolve_for_interaction_binds_current_empty_session(tmp_path):
    runtime, sessions, state, _, _ = _routing(tmp_path)
    binding = _binding()
    runtime.ensure_current(binding)

    record = runtime.resolve_for_interaction(
        binding,
        {
            "channel": "telegram",
            "conversation_key": "telegram:chat_1",
            "source": "chat_1",
            "runtime_timezone": "UTC",
        },
    )

    assert record is not None
    assert record.session_id == state["session_id"]
    persisted = sessions.get_session(state["session_id"])
    assert persisted.channel == "telegram"
    assert persisted.conversation_key == "telegram:chat_1"
    assert persisted.metadata == {"source": "chat_1", "runtime_timezone": "UTC"}


def test_resolve_for_interaction_switches_to_existing_route_session(tmp_path):
    runtime, sessions, state, activations, events = _routing(tmp_path)
    binding = _binding()
    runtime.ensure_current(binding)
    existing = sessions.create_session(
        session_id="session_existing",
        core_id="assistant",
        core_revision="rev_1",
        channel="telegram",
        conversation_key="telegram:chat_1",
        workspace="/workspace",
        provider="fake",
        model="fake/model",
    )

    record = runtime.resolve_for_interaction(
        binding,
        {"channel": "telegram", "conversation_key": "telegram:chat_1", "source": "chat_1"},
    )

    assert record == existing
    assert state["session_id"] == "session_existing"
    assert activations[-1] == "session_existing"
    assert events[-1] == {
        "type": "session.resumed",
        "core_id": "assistant",
        "core_revision": "rev_1",
        "channel": "telegram",
        "conversation_key": "telegram:chat_1",
    }


def test_resolve_for_interaction_creates_new_session_when_current_route_is_busy(tmp_path):
    runtime, sessions, state, _, events = _routing(tmp_path)
    binding = _binding()
    current = runtime.ensure_current(binding)
    sessions.update_session(
        current.session_id,
        channel="telegram",
        conversation_key="telegram:old",
    )
    sessions.append_message(current.session_id, role="user", content="old message")

    record = runtime.resolve_for_interaction(
        binding,
        {
            "channel": "telegram",
            "conversation_key": "telegram:new",
            "source": "chat_new",
            "reply_to": "msg_new",
        },
    )

    assert record is not None
    assert record.session_id != current.session_id
    assert state["session_id"] == record.session_id
    assert record.channel == "telegram"
    assert record.conversation_key == "telegram:new"
    assert record.metadata == {"source": "chat_new", "reply_to": "msg_new"}
    assert events[-1]["type"] == "session.created"


def test_start_new_can_replace_conversation_binding(tmp_path):
    runtime, sessions, _, _, _ = _routing(tmp_path)
    binding = _binding()
    old = sessions.create_session(
        session_id="session_old",
        core_id="assistant",
        core_revision="rev_1",
        channel="telegram",
        conversation_key="telegram:chat_1",
    )
    sessions.append_message(old.session_id, role="user", content="old")

    new = runtime.start_new(
        binding,
        channel="telegram",
        conversation_key="telegram:chat_1",
        source="chat_1",
        replace_conversation_binding=True,
    )

    assert new.session_id != old.session_id
    assert (
        sessions.resolve_interaction_session(
            core_id="assistant",
            channel="telegram",
            conversation_key="telegram:chat_1",
        )
        == new.session_id
    )
    assert new.metadata == {"source": "chat_1"}


def test_resume_missing_session_raises(tmp_path):
    runtime, _, _, _, _ = _routing(tmp_path)

    with pytest.raises(FileNotFoundError, match="session not found"):
        runtime.resume("session_missing")
