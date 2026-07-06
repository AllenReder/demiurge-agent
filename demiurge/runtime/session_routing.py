from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime_timezone import RuntimeTimezone
from demiurge.storage import SessionRecord


@dataclass(frozen=True, slots=True)
class SessionCoreBinding:
    core_id: str
    core_revision: str
    provider: str | None = None
    model: str | None = None
    workspace: str | None = None


class SessionRoutingRuntime:
    """Host-owned route-to-session policy.

    Adapters provide route facts; this module decides which durable session owns
    the turn.
    """

    def __init__(
        self,
        *,
        sessions: SessionRuntime,
        session_id: Callable[[], str],
        activate_session: Callable[[str], None],
        runtime_timezone: RuntimeTimezone,
        emit_event: Callable[..., dict[str, Any]],
    ) -> None:
        self.sessions = sessions
        self._session_id = session_id
        self._activate_session = activate_session
        self.runtime_timezone = runtime_timezone
        self._emit_event = emit_event

    def metadata_for(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if interaction is not None:
            metadata.update(
                {
                    "channel": interaction.channel,
                    "source": interaction.source,
                    "reply_to": interaction.reply_to,
                    "conversation_key": interaction.conversation_key,
                    **dict(interaction.metadata or {}),
                }
            )
        metadata.update(self.runtime_timezone.metadata())
        return {key: value for key, value in metadata.items() if value is not None}

    def ensure_current(self, binding: SessionCoreBinding) -> SessionRecord:
        record, created = self.sessions.ensure_session(
            self._session_id(),
            core_id=binding.core_id,
            core_revision=binding.core_revision,
            workspace=binding.workspace,
            provider=binding.provider,
            model=binding.model,
        )
        self._activate_session(record.session_id)
        self._emit_event(
            "session.created" if created else "session.resumed",
            core_id=binding.core_id,
            core_revision=binding.core_revision,
        )
        return record

    def start_new(
        self,
        binding: SessionCoreBinding,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
        replace_conversation_binding: bool = False,
    ) -> SessionRecord:
        metadata = self._record_metadata(source=source, reply_to=reply_to)
        bind_immediately = not (replace_conversation_binding and channel and conversation_key)
        record = self.sessions.create_session(
            core_id=binding.core_id,
            core_revision=binding.core_revision,
            channel=channel if bind_immediately else None,
            conversation_key=conversation_key if bind_immediately else None,
            workspace=binding.workspace,
            provider=binding.provider,
            model=binding.model,
            metadata=metadata,
        )
        if not bind_immediately:
            record = self.sessions.rebind_interaction_session(
                record.session_id,
                core_id=binding.core_id,
                core_revision=binding.core_revision,
                channel=channel,
                conversation_key=conversation_key,
                metadata=metadata,
            )
        self._activate_session(record.session_id)
        self._emit_event(
            "session.created",
            core_id=binding.core_id,
            core_revision=binding.core_revision,
            channel=channel,
            conversation_key=conversation_key,
        )
        return record

    def resume(self, session_id: str) -> SessionRecord:
        record = self.sessions.get_session(session_id)
        self._activate_session(record.session_id)
        self._emit_event(
            "session.resumed",
            core_id=record.core_id,
            core_revision=record.core_revision,
            channel=record.channel,
            conversation_key=record.conversation_key,
        )
        return record

    def resolve_for_interaction(self, binding: SessionCoreBinding, metadata: dict[str, Any]) -> SessionRecord | None:
        channel = metadata.get("channel")
        if not channel:
            return None

        channel = str(channel)
        conversation_key_value = metadata.get("conversation_key")
        if not conversation_key_value:
            if self.sessions.can_bind_session(self._session_id(), channel=channel, conversation_key=None):
                return self._update_current_binding(binding, channel=channel, conversation_key=None, metadata=metadata)
            return None

        conversation_key = str(conversation_key_value)
        existing = self.sessions.resolve_interaction_session(
            core_id=binding.core_id,
            channel=channel,
            conversation_key=conversation_key,
        )
        if existing:
            if existing != self._session_id():
                return self.resume(existing)
            return self.sessions.get_session(existing)

        if self.sessions.can_bind_session(self._session_id(), channel=channel, conversation_key=conversation_key):
            return self._update_current_binding(
                binding,
                channel=channel,
                conversation_key=conversation_key,
                metadata=metadata,
            )

        return self.start_new(
            binding,
            channel=channel,
            conversation_key=conversation_key,
            source=self._optional_text(metadata.get("source")),
            reply_to=self._optional_text(metadata.get("reply_to")),
        )

    def _update_current_binding(
        self,
        binding: SessionCoreBinding,
        *,
        channel: str,
        conversation_key: str | None,
        metadata: dict[str, Any],
    ) -> SessionRecord:
        return self.sessions.update_session(
            self._session_id(),
            core_id=binding.core_id,
            core_revision=binding.core_revision,
            channel=channel,
            conversation_key=conversation_key,
            metadata=self._binding_metadata(metadata),
        )

    @staticmethod
    def _record_metadata(*, source: str | None = None, reply_to: str | None = None) -> dict[str, Any]:
        return {key: value for key, value in {"source": source, "reply_to": reply_to}.items() if value is not None}

    @staticmethod
    def _binding_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in metadata.items() if key not in {"channel", "conversation_key"}}

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)
