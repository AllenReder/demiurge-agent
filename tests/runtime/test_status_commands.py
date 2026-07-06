from __future__ import annotations

from types import SimpleNamespace

from demiurge.runtime.status_commands import (
    build_runtime_status_view,
    format_runtime_status_markdown,
    runtime_status_key_values,
)


class MessageCounter:
    def __init__(self, count: int | Exception):
        self.count = count
        self.calls = []

    def message_count(self, session_id: str) -> int:
        self.calls.append(session_id)
        if isinstance(self.count, Exception):
            raise self.count
        return self.count


def _runner(**kwargs):
    values = {
        "core_id": "assistant",
        "session_id": "session_1",
        "provider_name": "fake",
        "runtime_timezone": SimpleNamespace(name="Asia/Shanghai", source="host"),
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_build_runtime_status_view_reads_message_count_and_runtime_fields():
    counter = MessageCounter(7)

    view = build_runtime_status_view(
        _runner(),
        counter,
        running=True,
        busy_mode="queue",
        queued_inputs=2,
        channel="telegram",
    )

    assert counter.calls == ["session_1"]
    assert view.channel == "telegram"
    assert view.core_id == "assistant"
    assert view.session_id == "session_1"
    assert view.running is True
    assert view.status_text == "running"
    assert view.message_count == 7
    assert view.provider == "fake"
    assert view.runtime_timezone is not None
    assert view.runtime_timezone.name == "Asia/Shanghai"
    assert view.runtime_timezone.source == "host"


def test_build_runtime_status_view_tolerates_message_count_failure():
    view = build_runtime_status_view(
        _runner(),
        MessageCounter(RuntimeError("database unavailable")),
        running=False,
        busy_mode="interrupt",
        queued_inputs=0,
    )

    assert view.message_count is None
    assert view.status_text == "idle"


def test_format_runtime_status_markdown_appends_extra_lines_before_optional_fields():
    view = build_runtime_status_view(
        _runner(),
        MessageCounter(3),
        running=True,
        busy_mode="interrupt",
        queued_inputs=1,
    )

    text = format_runtime_status_markdown(view, extra_lines=("- access: `restricted`",))

    assert "- core: `assistant`" in text
    assert "- running: `true`" in text
    assert "- access: `restricted`" in text
    assert text.index("- access: `restricted`") < text.index("- messages: `3`")
    assert "- provider: `fake`" in text
    assert "- runtime timezone: `Asia/Shanghai` (host)" in text


def test_runtime_status_key_values_preserve_tui_command_fields():
    view = build_runtime_status_view(
        _runner(provider_name=None, runtime_timezone=None),
        MessageCounter(5),
        running=False,
        busy_mode="queue",
        queued_inputs=4,
    )

    values = runtime_status_key_values(view, extra=(("tool_display", "full"),))

    assert values == {
        "core_id": "assistant",
        "session_id": "session_1",
        "current_status": "idle",
        "busy_mode": "queue",
        "queued_inputs": 4,
        "message_count": 5,
        "tool_display": "full",
    }
