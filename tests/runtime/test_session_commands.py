from __future__ import annotations

from demiurge.runtime.session_commands import (
    build_session_list_view,
    format_sessions_markdown,
    format_sessions_table,
    resolve_session_choice,
    resume_bound_session,
    session_list_view,
    session_record_dict,
    start_bound_session,
)
from demiurge.storage import SessionRecord


def _record(session_id: str, *, updated_at: str = "2026-07-06T04:00:00Z", channel: str = "tui", preview: str = ""):
    return SessionRecord(
        session_id=session_id,
        core_id="assistant",
        core_revision="rev_1",
        created_at="2026-07-06T03:00:00Z",
        updated_at=updated_at,
        channel=channel,
        message_count=3,
        preview=preview,
    )


class FakeSessionRuntime:
    def __init__(self):
        self.calls = []

    def list_owned_sessions(
        self,
        scope,
        *,
        core_id: str | None = None,
        limit: int = 20,
    ):
        self.calls.append({"scope": scope, "core_id": core_id, "limit": limit})
        return [_record("session_1"), _record("session_2")]


class FakeRouteBinding:
    def __init__(self):
        self.binds = []

    def bind(self, router, session_id: str):
        self.binds.append((router, session_id))
        return {"router": router, "session_id": session_id}


class FakeRunner:
    def __init__(self, *, start_session_id: str = "session_new", missing: bool = False):
        self.interaction_router = object()
        self.start_session_id = start_session_id
        self.missing = missing
        self.prepared = False
        self.start_calls = []
        self.resume_calls = []

    async def prepare_live_core(self):
        self.prepared = True

    def start_new_session(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.start_session_id

    def resume_session(self, session_id: str, **kwargs) -> None:
        self.resume_calls.append({"session_id": session_id, **kwargs})
        if self.missing:
            raise FileNotFoundError(f"session not found: {session_id}")


class NoStartRunner:
    pass


def test_build_session_list_view_reads_runtime_with_core_and_limit():
    runtime = FakeSessionRuntime()
    scope = object()

    view = build_session_list_view(
        runtime,
        principal_scope=scope,
        core_id="assistant",
        active_session_id="session_1",
        limit=5,
    )

    assert runtime.calls == [{"scope": scope, "core_id": "assistant", "limit": 5}]
    assert view.choices[0].active is True
    assert view.session_ids == ["session_1", "session_2"]


def test_session_list_view_empty_texts():
    view = session_list_view([], active_session_id="session_1")

    assert view.records == []
    assert view.session_ids == []
    assert format_sessions_markdown(view) == "No sessions found."
    assert "session_id" in format_sessions_table(view)


def test_session_list_view_marks_active_session_and_exports_records():
    view = session_list_view(
        [
            _record("session_1", preview="hello"),
            _record("session_2", channel="telegram"),
        ],
        active_session_id="session_2",
    )

    assert view.session_ids == ["session_1", "session_2"]
    assert view.choices[0].active is False
    assert view.choices[1].active is True
    assert session_record_dict(view.choices[1].record)["channel"] == "telegram"
    assert "2. * `session_2`" in view.text()
    assert "session_2" in view.text(table=True)


def test_resolve_session_choice_accepts_numeric_choices():
    view = session_list_view([_record("session_1"), _record("session_2")])

    resolution = resolve_session_choice("2", view)

    assert resolution.ok is True
    assert resolution.session_id == "session_2"


def test_resolve_session_choice_reports_out_of_range_numeric_choice():
    view = session_list_view([_record("session_1")])

    resolution = resolve_session_choice("2", view)

    assert resolution.ok is False
    assert resolution.kind == "out_of_range"
    assert resolution.message == "Session number out of range: 2"


def test_resolve_session_choice_strips_wrapped_session_ids():
    view = session_list_view([_record("session_1")])

    assert resolve_session_choice("`session_1`", view).session_id == "session_1"
    assert resolve_session_choice("<session_1>", view).session_id == "session_1"
    assert resolve_session_choice('"session_1"', view).session_id == "session_1"
    assert resolve_session_choice("[session_1]", view).session_id == "session_1"


async def test_start_bound_session_prepares_starts_and_binds_route():
    runner = FakeRunner(start_session_id="session_created")
    binding = FakeRouteBinding()

    result = await start_bound_session(
        runner,
        binding,
        channel="telegram",
        conversation_key="telegram:dm:1",
        source="1",
        reply_to="2",
        replace_conversation_binding=True,
    )

    assert result.ok is True
    assert result.session_id == "session_created"
    assert runner.prepared is True
    assert runner.start_calls == [
        {
            "channel": "telegram",
            "conversation_key": "telegram:dm:1",
            "source": "1",
            "reply_to": "2",
            "replace_conversation_binding": True,
        }
    ]
    assert binding.binds == [(runner.interaction_router, "session_created")]


async def test_start_bound_session_reports_unavailable_runner_without_binding():
    binding = FakeRouteBinding()

    result = await start_bound_session(NoStartRunner(), binding, channel="text")

    assert result.ok is False
    assert result.session_id is None
    assert result.message == "Session reset is not available."
    assert binding.binds == []


def test_resume_bound_session_resumes_and_binds_route():
    runner = FakeRunner()
    binding = FakeRouteBinding()

    result = resume_bound_session(
        runner,
        binding,
        "session_existing",
        channel="telegram",
        conversation_key="telegram:dm:1",
        source="1",
        reply_to="2",
        replace_conversation_binding=True,
    )

    assert result.ok is True
    assert result.session_id == "session_existing"
    assert runner.resume_calls == [
        {
            "session_id": "session_existing",
            "channel": "telegram",
            "conversation_key": "telegram:dm:1",
            "source": "1",
            "reply_to": "2",
            "replace_conversation_binding": True,
        }
    ]
    assert binding.binds == [(runner.interaction_router, "session_existing")]


def test_resume_bound_session_reports_missing_session_without_binding():
    runner = FakeRunner(missing=True)
    binding = FakeRouteBinding()

    result = resume_bound_session(runner, binding, "missing")

    assert result.ok is False
    assert result.session_id is None
    assert result.message == "session not found: missing"
    assert binding.binds == []
