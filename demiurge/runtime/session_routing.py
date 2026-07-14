from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from demiurge.runtime.conversation_keys import build_conversation_key
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.scope import AuthorityKind, PrincipalScope, PrincipalScopeResolver
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime_timezone import RuntimeTimezone
from demiurge.storage import SessionRecord
from demiurge.util import utc_id


_HOST_AUTHORITY_METADATA_KEYS = frozenset(
    {
        "allowed_session_ids",
        "authority",
        "origin_session_id",
        "origin_turn_id",
        "principal_id",
        "principal_key",
        "principal_scope",
        "session_id",
    }
)


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
            adapter_metadata = dict(interaction.metadata or {})
            for key in _HOST_AUTHORITY_METADATA_KEYS:
                adapter_metadata.pop(key, None)
            metadata.update(adapter_metadata)
            metadata.update(
                {
                    "channel": interaction.channel,
                    "source": interaction.source,
                    "reply_to": interaction.reply_to,
                    "conversation_key": interaction.conversation_key,
                }
            )
        metadata.update(self.runtime_timezone.metadata())
        return {key: value for key, value in metadata.items() if value is not None}

    def ensure_current(
        self,
        binding: SessionCoreBinding,
        *,
        principal_scope: PrincipalScope | None = None,
    ) -> SessionRecord:
        record, created = self.sessions.ensure_session(
            self._session_id(),
            core_id=binding.core_id,
            core_revision=binding.core_revision,
            workspace=binding.workspace,
            provider=binding.provider,
            model=binding.model,
            principal_scope=principal_scope,
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
        principal_scope: PrincipalScope | None = None,
        route_metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        metadata = self._record_metadata(source=source, reply_to=reply_to)
        if route_metadata:
            metadata.update(self._binding_metadata(route_metadata))
        bind_immediately = not (replace_conversation_binding and channel and conversation_key)
        record = self.sessions.create_session(
            session_id=principal_scope.session_id if principal_scope is not None else None,
            core_id=binding.core_id,
            core_revision=binding.core_revision,
            channel=channel if bind_immediately else None,
            conversation_key=conversation_key if bind_immediately else None,
            workspace=binding.workspace,
            provider=binding.provider,
            model=binding.model,
            metadata=metadata,
            principal_scope=principal_scope,
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

    def resume(
        self,
        session_id: str,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
        replace_conversation_binding: bool = False,
        principal_scope: PrincipalScope | None = None,
    ) -> SessionRecord:
        if principal_scope is not None:
            resolver = PrincipalScopeResolver(self.sessions.store)
            resolver.validate_owned(principal_scope)
            if principal_scope.authority is AuthorityKind.OPERATOR:
                if not self.sessions.store.session_owner_exists(session_id):
                    raise FileNotFoundError(f"session not found: {session_id}")
            elif not principal_scope.allows_session(session_id):
                raise FileNotFoundError(f"session not found: {session_id}")
        record = self.sessions.get_session(session_id)
        if replace_conversation_binding and channel and conversation_key:
            record = self.sessions.rebind_interaction_session(
                record.session_id,
                core_id=record.core_id,
                core_revision=record.core_revision,
                channel=str(channel),
                conversation_key=str(conversation_key),
                metadata=self._record_metadata(source=source, reply_to=reply_to),
            )
        self._activate_session(record.session_id)
        self._emit_event(
            "session.resumed",
            core_id=record.core_id,
            core_revision=record.core_revision,
            channel=record.channel,
            conversation_key=record.conversation_key,
        )
        return record

    def resolve_for_interaction(
        self,
        binding: SessionCoreBinding,
        interaction: InteractionInbound | None,
        metadata: dict[str, Any],
        *,
        fixed_scope: PrincipalScope | None = None,
    ) -> PrincipalScope:
        resolver = PrincipalScopeResolver(self.sessions.store)
        if interaction is not None and interaction.principal_scope is not None:
            internal_scope = interaction.principal_scope
            resolver.validate_owned(internal_scope)
            if internal_scope.session_id != self._session_id():
                self.resume(
                    internal_scope.session_id,
                    principal_scope=internal_scope,
                )
            return internal_scope
        use_fixed_scope = fixed_scope is not None and (
            fixed_scope.authority is not AuthorityKind.OPERATOR
            or interaction is None
            or interaction.channel == "tui"
        )
        if use_fixed_scope:
            assert fixed_scope is not None
            return self._resolve_fixed_scope(
                binding,
                metadata=metadata,
                fixed_scope=fixed_scope,
                resolver=resolver,
            )
        if interaction is None:
            return resolver.local_operator(
                active_session_id=self._session_id(),
                reason="admit local operator turn without interaction",
            )
        channel = metadata.get("channel")
        if not channel:
            return resolver.local_operator(
                active_session_id=self._session_id(),
                reason="admit local operator turn without channel",
            )

        channel = str(channel)
        principal_key = str(interaction.principal_key or interaction.conversation_key or interaction.source or "")
        if not principal_key:
            raise ValueError("external interaction principal_key is required")
        conversation_key = str(
            interaction.conversation_key
            or build_conversation_key(channel, "principal", principal_key)
        )

        existing = self.sessions.resolve_interaction_session(
            core_id=binding.core_id,
            channel=channel,
            conversation_key=conversation_key,
        )
        if existing:
            scope = resolver.conversation(
                channel=channel,
                principal_key=principal_key,
                conversation_key=conversation_key,
                session_id=existing,
            )
            if existing != self._session_id():
                self.resume(existing)
            return scope

        current_scope = resolver.issue_conversation(
            channel=channel,
            principal_key=principal_key,
            conversation_key=conversation_key,
            session_id=self._session_id(),
        )
        try:
            resolver.conversation(
                channel=channel,
                principal_key=principal_key,
                conversation_key=conversation_key,
                session_id=self._session_id(),
            )
        except FileNotFoundError:
            pass
        else:
            if self.sessions.can_bind_session(
                self._session_id(),
                channel=channel,
                conversation_key=conversation_key,
            ):
                self._update_current_binding(
                    binding,
                    channel=channel,
                    conversation_key=conversation_key,
                    metadata=metadata,
                )
                return resolver.conversation(
                    channel=channel,
                    principal_key=principal_key,
                    conversation_key=conversation_key,
                    session_id=self._session_id(),
                )

        new_session_id = utc_id("session_")
        scope = resolver.issue_conversation(
            channel=channel,
            principal_key=principal_key,
            conversation_key=conversation_key,
            session_id=new_session_id,
        )
        self.start_new(
            binding,
            channel=channel,
            conversation_key=conversation_key,
            source=self._optional_text(metadata.get("source")),
            reply_to=self._optional_text(metadata.get("reply_to")),
            principal_scope=scope,
            route_metadata=metadata,
        )
        return resolver.conversation(
            channel=channel,
            principal_key=principal_key,
            conversation_key=conversation_key,
            session_id=new_session_id,
        )

    def _resolve_fixed_scope(
        self,
        binding: SessionCoreBinding,
        *,
        metadata: dict[str, Any],
        fixed_scope: PrincipalScope,
        resolver: PrincipalScopeResolver,
    ) -> PrincipalScope:
        fixed_scope = resolver.admit(fixed_scope)
        if fixed_scope.authority is AuthorityKind.OPERATOR:
            channel = self._optional_text(metadata.get("channel"))
            conversation_key = self._optional_text(metadata.get("conversation_key"))
            if channel and conversation_key:
                existing = self.sessions.resolve_interaction_session(
                    core_id=binding.core_id,
                    channel=channel,
                    conversation_key=conversation_key,
                )
                if existing is not None and existing != self._session_id():
                    self.resume(existing)
                elif self.sessions.can_bind_session(
                    self._session_id(),
                    channel=channel,
                    conversation_key=conversation_key,
                ):
                    self._update_current_binding(
                        binding,
                        channel=channel,
                        conversation_key=conversation_key,
                        metadata=metadata,
                    )
            return resolver.local_operator(
                active_session_id=self._session_id(),
                reason="refresh fixed operator turn scope",
            )

        if fixed_scope.session_id != self._session_id():
            raise RuntimeError("fixed PrincipalScope session does not match runner session")
        channel = self._optional_text(metadata.get("channel"))
        conversation_key = self._optional_text(metadata.get("conversation_key"))
        if channel and self.sessions.can_bind_session(
            self._session_id(),
            channel=channel,
            conversation_key=conversation_key,
        ):
            self._update_current_binding(
                binding,
                channel=channel,
                conversation_key=conversation_key,
                metadata=metadata,
            )
        return fixed_scope

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
