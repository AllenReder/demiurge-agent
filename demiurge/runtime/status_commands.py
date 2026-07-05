from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from demiurge.runtime.text_format import format_key_values


class MessageCountRuntime(Protocol):
    def message_count(self, session_id: str) -> int:
        ...


@dataclass(frozen=True, slots=True)
class RuntimeTimezoneView:
    name: str
    source: str

    @classmethod
    def from_value(cls, value: Any) -> "RuntimeTimezoneView | None":
        if value is None:
            return None
        name = getattr(value, "name", None)
        source = getattr(value, "source", None)
        if name is None:
            return None
        return cls(name=str(name), source=str(source or "unknown"))


@dataclass(frozen=True, slots=True)
class RuntimeStatusView:
    running: bool
    busy_mode: str
    queued_inputs: int
    channel: str | None = None
    core_id: str = "?"
    session_id: str = "?"
    message_count: int | None = None
    provider: str | None = None
    runtime_timezone: RuntimeTimezoneView | None = None

    @property
    def running_text(self) -> str:
        return str(self.running).lower()

    @property
    def status_text(self) -> str:
        return "running" if self.running else "idle"


def build_runtime_status_view(
    runner: Any,
    session_runtime: MessageCountRuntime | None,
    *,
    running: bool,
    busy_mode: str,
    queued_inputs: int,
    channel: str | None = None,
) -> RuntimeStatusView:
    session_id = str(getattr(runner, "session_id", "?"))
    message_count: int | None = None
    if session_runtime is not None and session_id and session_id != "?":
        with contextlib.suppress(Exception):
            message_count = session_runtime.message_count(session_id)
    provider = getattr(runner, "provider_name", None)
    return RuntimeStatusView(
        channel=channel,
        core_id=str(getattr(runner, "core_id", "?")),
        session_id=session_id,
        running=running,
        busy_mode=str(busy_mode),
        queued_inputs=int(queued_inputs),
        message_count=message_count,
        provider=str(provider) if provider else None,
        runtime_timezone=RuntimeTimezoneView.from_value(getattr(runner, "runtime_timezone", None)),
    )


def format_runtime_status_markdown(view: RuntimeStatusView, *, extra_lines: Iterable[str] = ()) -> str:
    lines = ["# Status"]
    if view.channel is not None:
        lines.append(f"- channel: `{view.channel}`")
    lines.extend(
        [
            f"- core: `{view.core_id}`",
            f"- session: `{view.session_id}`",
            f"- running: `{view.running_text}`",
            f"- busy mode: `{view.busy_mode}`",
            f"- queued: `{view.queued_inputs}`",
        ]
    )
    lines.extend(extra_lines)
    if view.message_count is not None:
        lines.append(f"- messages: `{view.message_count}`")
    if view.provider:
        lines.append(f"- provider: `{view.provider}`")
    if view.runtime_timezone is not None:
        lines.append(f"- runtime timezone: `{view.runtime_timezone.name}` ({view.runtime_timezone.source})")
    return "\n".join(lines)


def runtime_status_key_values(view: RuntimeStatusView, *, extra: Iterable[tuple[str, Any]] = ()) -> dict[str, Any]:
    values: dict[str, Any] = {
        "core_id": view.core_id,
        "session_id": view.session_id,
        "current_status": view.status_text,
        "busy_mode": view.busy_mode,
        "queued_inputs": view.queued_inputs,
    }
    if view.channel is not None:
        values["channel"] = view.channel
    if view.message_count is not None:
        values["message_count"] = view.message_count
    if view.provider:
        values["provider"] = view.provider
    if view.runtime_timezone is not None:
        values["runtime_timezone"] = view.runtime_timezone.name
        values["runtime_timezone_source"] = view.runtime_timezone.source
    for key, value in extra:
        values[str(key)] = value
    return values
