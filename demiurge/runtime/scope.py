from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Any
from weakref import ReferenceType, WeakKeyDictionary, ref

from demiurge.runtime.conversation_keys import build_conversation_key


class AuthorityKind(StrEnum):
    CONVERSATION = "conversation"
    OPERATOR = "operator"
    SYSTEM = "system"
    DELEGATED_AGENT = "delegated_agent"


@dataclass(frozen=True, slots=True)
class _PersistedScopeRecord:
    principal_id: str
    authority: AuthorityKind
    channel: str | None
    conversation_key: str | None
    session_id: str
    allowed_session_ids: tuple[str, ...]
    origin_session_id: str | None = None
    origin_turn_id: str | None = None

    @classmethod
    def from_scope(cls, scope: PrincipalScope) -> _PersistedScopeRecord:
        return cls(
            principal_id=scope.principal_id,
            authority=scope.authority,
            channel=scope.channel,
            conversation_key=scope.conversation_key,
            session_id=scope.session_id,
            allowed_session_ids=tuple(sorted(scope.allowed_session_ids)),
            origin_session_id=scope.origin_session_id,
            origin_turn_id=scope.origin_turn_id,
        )

    @classmethod
    def decode(cls, record: dict[str, Any]) -> _PersistedScopeRecord:
        allowed_session_ids = record.get("allowed_session_ids")
        if not isinstance(allowed_session_ids, list):
            raise ValueError("persisted PrincipalScope.allowed_session_ids must be a list")
        return cls(
            principal_id=str(record.get("principal_id") or ""),
            authority=AuthorityKind(str(record.get("authority") or "")),
            channel=_optional_text(record.get("channel")),
            conversation_key=_optional_text(record.get("conversation_key")),
            session_id=str(record.get("session_id") or ""),
            allowed_session_ids=tuple(str(value) for value in allowed_session_ids),
            origin_session_id=_optional_text(record.get("origin_session_id")),
            origin_turn_id=_optional_text(record.get("origin_turn_id")),
        )

    def encode(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "authority": self.authority.value,
            "channel": self.channel,
            "conversation_key": self.conversation_key,
            "session_id": self.session_id,
            "allowed_session_ids": list(self.allowed_session_ids),
            "origin_session_id": self.origin_session_id,
            "origin_turn_id": self.origin_turn_id,
        }

    def to_scope(
        self,
        *,
        issuer: object,
        owner_validated: bool = False,
        operator_authority: object | None = None,
    ) -> PrincipalScope:
        return PrincipalScope(
            principal_id=self.principal_id,
            authority=self.authority,
            channel=self.channel,
            conversation_key=self.conversation_key,
            session_id=self.session_id,
            allowed_session_ids=frozenset(self.allowed_session_ids),
            origin_session_id=self.origin_session_id,
            origin_turn_id=self.origin_turn_id,
            _issuer=issuer,
            _owner_validated=owner_validated,
            _operator_authority=operator_authority,
            _factory=_HOST_AUTHORITY_FACTORY,
        )


_HOST_AUTHORITY_FACTORY = object()
_OPERATOR_AUTHORITIES: WeakKeyDictionary[
    Any,
    tuple[object, ReferenceType[Any]],
] = WeakKeyDictionary()
_OPERATOR_AUTHORITIES_LOCK = RLock()


def _activate_operator_authority(store: Any, host: Any) -> object:
    """Create the process-local operator capability for one active Host."""
    from demiurge.app import DemiurgeApp, _is_active_app_lifecycle

    if (
        not isinstance(host, DemiurgeApp)
        or host.runtime_store is not store
        or not _is_active_app_lifecycle(host)
    ):
        raise PermissionError("operator authority activation requires its owning DemiurgeApp")
    with _OPERATOR_AUTHORITIES_LOCK:
        if _active_operator_authority(store) is not None:
            raise RuntimeError("operator authority is already active for this RuntimeStore")
        authority = object()
        _OPERATOR_AUTHORITIES[store] = (authority, ref(host))
        return authority


def _deactivate_operator_authority(store: Any, host: Any, authority: object) -> None:
    """Revoke a Host capability without affecting a newer Host instance."""
    with _OPERATOR_AUTHORITIES_LOCK:
        entry = _OPERATOR_AUTHORITIES.get(store)
        if entry is not None and entry[0] is authority and entry[1]() is host:
            del _OPERATOR_AUTHORITIES[store]


def _active_operator_authority(store: Any) -> object | None:
    with _OPERATOR_AUTHORITIES_LOCK:
        entry = _OPERATOR_AUTHORITIES.get(store)
        if entry is None:
            return None
        authority, host_ref = entry
        host = host_ref()
        if (
            host is None
            or host.runtime_store is not store
            or host._closed
            or host._operator_authority is not authority
        ):
            del _OPERATOR_AUTHORITIES[store]
            return None
        return authority


@dataclass(frozen=True, slots=True, init=False)
class PrincipalScope:
    principal_id: str
    authority: AuthorityKind
    channel: str | None
    conversation_key: str | None
    session_id: str
    allowed_session_ids: frozenset[str]
    origin_session_id: str | None = None
    origin_turn_id: str | None = None
    _issuer: object = field(repr=False, compare=False)
    _owner_validated: bool = field(repr=False, compare=False)
    _operator_authority: object | None = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        principal_id: str,
        authority: AuthorityKind,
        channel: str | None,
        conversation_key: str | None,
        session_id: str,
        allowed_session_ids: frozenset[str],
        origin_session_id: str | None = None,
        origin_turn_id: str | None = None,
        _issuer: object | None = None,
        _owner_validated: bool = False,
        _operator_authority: object | None = None,
        _factory: object | None = None,
    ) -> None:
        if _factory is not _HOST_AUTHORITY_FACTORY:
            raise TypeError("PrincipalScope must be constructed by a Host authority factory")
        if _issuer is None:
            raise TypeError("PrincipalScope must be issued by a Host authority resolver")
        if not principal_id:
            raise ValueError("principal_id is required")
        if not session_id:
            raise ValueError("session_id is required")
        bounded_session_ids = frozenset(allowed_session_ids)
        if session_id not in bounded_session_ids:
            raise ValueError("active session must be included in allowed_session_ids")
        if authority is AuthorityKind.CONVERSATION:
            if not channel or not conversation_key or bounded_session_ids != {session_id}:
                raise ValueError("conversation authority must be route-bound to one session")
        elif authority is AuthorityKind.OPERATOR:
            if principal_id != build_conversation_key("principal", "operator", "local"):
                raise ValueError("operator authority principal is Host-defined")
            if channel is not None or conversation_key is not None:
                raise ValueError("operator authority cannot carry conversation route facts")
            if origin_session_id is not None or origin_turn_id is not None:
                raise ValueError("operator authority cannot carry delegated lineage")
            if _operator_authority is None:
                raise TypeError("operator PrincipalScope requires an active Host authority")
        elif authority is AuthorityKind.SYSTEM:
            if channel is not None or conversation_key is not None or bounded_session_ids != {session_id}:
                raise ValueError("system authority must be limited to one run session")
        elif authority is AuthorityKind.DELEGATED_AGENT:
            if (
                channel is not None
                or conversation_key is not None
                or bounded_session_ids != {session_id}
                or not origin_session_id
                or not origin_turn_id
            ):
                raise ValueError("delegated authority requires bounded parent lineage")
        object.__setattr__(self, "principal_id", principal_id)
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "conversation_key", conversation_key)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "allowed_session_ids", bounded_session_ids)
        object.__setattr__(self, "origin_session_id", origin_session_id)
        object.__setattr__(self, "origin_turn_id", origin_turn_id)
        object.__setattr__(self, "_issuer", _issuer)
        object.__setattr__(self, "_owner_validated", bool(_owner_validated))
        object.__setattr__(self, "_operator_authority", _operator_authority)

    def allows_session(self, session_id: str) -> bool:
        return session_id in self.allowed_session_ids

    def to_record(self) -> dict[str, Any]:
        return _PersistedScopeRecord.from_scope(self).encode()

    def redacted_view(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "authority": self.authority.value,
            "channel": self.channel,
            "conversation_key": self.conversation_key,
            "session_id": self.session_id,
            "allowed_session_count": len(self.allowed_session_ids),
        }


class PrincipalScopeResolver:
    def __init__(self, store: Any):
        self.store = store
        self._issuer = store._principal_scope_issuer

    def issue_conversation(
        self,
        *,
        channel: str,
        principal_key: str,
        conversation_key: str,
        session_id: str,
    ) -> PrincipalScope:
        return _conversation_scope(
            channel=channel,
            principal_key=principal_key,
            conversation_key=conversation_key,
            session_id=session_id,
            issuer=self._issuer,
        )

    def conversation(
        self,
        *,
        channel: str,
        principal_key: str,
        conversation_key: str,
        session_id: str,
    ) -> PrincipalScope:
        from demiurge.runtime.store import RuntimeQuery

        candidate = self.issue_conversation(
            channel=channel,
            principal_key=principal_key,
            conversation_key=conversation_key,
            session_id=session_id,
        )
        rows = self.store.query(
            RuntimeQuery(
                table="session_owners",
                where={
                    "session_id": session_id,
                    "owner_kind": AuthorityKind.CONVERSATION.value,
                    "principal_id": candidate.principal_id,
                    "channel": channel,
                    "conversation_key": conversation_key,
                },
                limit=1,
            )
        ).rows
        if not rows:
            raise FileNotFoundError(f"session not found: {session_id}")
        return _copy_scope(candidate, owner_validated=True)

    def scheduled_run(
        self,
        *,
        core_id: str,
        schedule_id: str,
        run_id: str,
        session_id: str,
    ) -> PrincipalScope:
        return PrincipalScope(
            principal_id=build_conversation_key(
                "principal",
                "scheduler",
                core_id,
                schedule_id,
                run_id,
            ),
            authority=AuthorityKind.SYSTEM,
            channel=None,
            conversation_key=None,
            session_id=session_id,
            allowed_session_ids=frozenset({session_id}),
            _issuer=self._issuer,
            _factory=_HOST_AUTHORITY_FACTORY,
        )

    def delegated_agent(
        self,
        *,
        parent: PrincipalScope,
        task_id: str,
        parent_turn_id: str,
        child_session_id: str,
    ) -> PrincipalScope:
        self.validate_owned(parent)
        return PrincipalScope(
            principal_id=build_conversation_key(
                "principal",
                "delegated_agent",
                parent.principal_id,
                task_id,
            ),
            authority=AuthorityKind.DELEGATED_AGENT,
            channel=None,
            conversation_key=None,
            session_id=child_session_id,
            allowed_session_ids=frozenset({child_session_id}),
            origin_session_id=parent.session_id,
            origin_turn_id=parent_turn_id,
            _issuer=self._issuer,
            _factory=_HOST_AUTHORITY_FACTORY,
        )

    def capture_origin_record(
        self,
        *,
        scope: PrincipalScope,
        owner_session_id: str,
    ) -> dict[str, Any]:
        self.validate_owned(scope)
        if scope.session_id != owner_session_id:
            raise PermissionError("PrincipalScope does not own the background task session")
        owner = self._owner_row(owner_session_id)
        self._validate_origin_authority(scope, owner)
        return _PersistedScopeRecord.from_scope(scope).encode()

    def background_completion(
        self,
        *,
        origin_record: dict[str, Any],
        owner_session_id: str,
    ) -> PrincipalScope:
        persisted = _PersistedScopeRecord.decode(origin_record)
        if persisted.allowed_session_ids != (owner_session_id,):
            raise PermissionError("background completion scope must be bounded to its owner session")
        owner = self._owner_row(owner_session_id)
        if str(owner.get("owner_kind") or "") == "legacy_local":
            raise PermissionError(
                f"legacy session owner requires explicit operator repair: {owner_session_id}"
            )
        operator_authority = None
        if persisted.authority is AuthorityKind.OPERATOR:
            operator_authority = self._require_operator_authority()
        scope = persisted.to_scope(
            issuer=self._issuer,
            operator_authority=operator_authority,
        )
        if scope.session_id != owner_session_id:
            raise PermissionError("background completion scope does not match owner session")
        self._validate_origin_authority(scope, owner)
        return _copy_scope(scope, owner_validated=True)

    def local_operator(
        self,
        *,
        active_session_id: str,
        reason: str,
        allow_unowned_active: bool = False,
    ) -> PrincipalScope:
        from demiurge.runtime.store import RuntimeEvent

        operator_authority = self._require_operator_authority()
        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise ValueError("operator scope reason is required")
        if len(normalized_reason) > 200:
            raise ValueError("operator scope reason must not exceed 200 characters")
        owner_exists = self.store.session_owner_exists(active_session_id)
        if not owner_exists:
            if not allow_unowned_active:
                raise FileNotFoundError(f"session not found: {active_session_id}")
        scope = _operator_scope(
            active_session_id=active_session_id,
            owner_validated=owner_exists,
            issuer=self._issuer,
            operator_authority=operator_authority,
        )
        self.store.append(
            [
                RuntimeEvent(
                    type="principal_scope.operator_issued",
                    aggregate_type="principal_scope",
                    aggregate_id=scope.principal_id,
                    payload={
                        "reason": normalized_reason,
                        "active_session_id": active_session_id,
                        "visible_session_count": self.store.session_owner_count()
                        + (0 if owner_exists else 1),
                    },
                    actor={"kind": "operator", "principal_id": scope.principal_id},
                )
            ]
        )
        return scope

    def origin_scope(self, *, session_id: str) -> PrincipalScope:
        row = self._owner_row(session_id)
        owner_kind = str(row.get("owner_kind") or "")
        if owner_kind == "legacy_local":
            raise PermissionError(
                f"legacy session owner requires explicit operator repair: {session_id}"
            )
        if owner_kind == AuthorityKind.OPERATOR.value:
            return self.local_operator(
                active_session_id=session_id,
                reason="restore persisted operator origin",
            )
        return _PersistedScopeRecord(
            principal_id=str(row.get("principal_id") or ""),
            authority=AuthorityKind(owner_kind),
            channel=_optional_text(row.get("channel")),
            conversation_key=_optional_text(row.get("conversation_key")),
            session_id=session_id,
            allowed_session_ids=(session_id,),
            origin_session_id=_optional_text(row.get("origin_session_id")),
            origin_turn_id=_optional_text(row.get("origin_turn_id")),
        ).to_scope(
            issuer=self._issuer,
            owner_validated=True,
        )

    def validate(self, scope: PrincipalScope) -> None:
        if scope._issuer is not self._issuer:
            raise PermissionError("PrincipalScope was not issued by this RuntimeStore")
        if scope.authority is AuthorityKind.OPERATOR:
            self._validate_operator_authority(scope)

    def validate_owned(self, scope: PrincipalScope) -> None:
        self.validate(scope)
        if not scope._owner_validated:
            raise PermissionError("PrincipalScope has not been validated against its durable owner")

    def admit(self, scope: PrincipalScope) -> PrincipalScope:
        self.validate(scope)
        owner = self._owner_row(scope.session_id)
        self._validate_origin_authority(scope, owner)
        return _copy_scope(scope, owner_validated=True)

    def _owner_row(self, session_id: str) -> dict[str, Any]:
        from demiurge.runtime.store import RuntimeQuery

        rows = self.store.query(
            RuntimeQuery(
                table="session_owners",
                where={"session_id": session_id},
                limit=1,
            )
        ).rows
        if not rows:
            raise FileNotFoundError(f"session not found: {session_id}")
        return rows[0]

    def _validate_origin_authority(
        self,
        scope: PrincipalScope,
        owner: dict[str, Any],
    ) -> None:
        if scope.authority is AuthorityKind.OPERATOR:
            self._validate_operator_authority(scope)
            return
        self._validate_owner_match(scope, owner)

    def _require_operator_authority(self) -> object:
        authority = _active_operator_authority(self.store)
        if authority is None:
            raise PermissionError("operator authority is available only inside the active Host")
        return authority

    def _validate_operator_authority(self, scope: PrincipalScope) -> None:
        authority = self._require_operator_authority()
        if scope._operator_authority is not authority:
            raise PermissionError("operator PrincipalScope was not issued by the active Host")
        if not self.store.has_operator_scope_audit(
            active_session_id=scope.session_id,
            principal_id=scope.principal_id,
        ):
            raise PermissionError("operator PrincipalScope has no durable issuance audit")

    @staticmethod
    def _validate_owner_match(scope: PrincipalScope, owner: dict[str, Any]) -> None:
        expected = {
            "authority": str(owner.get("owner_kind") or ""),
            "principal_id": str(owner.get("principal_id") or ""),
            "channel": _optional_text(owner.get("channel")),
            "conversation_key": _optional_text(owner.get("conversation_key")),
            "origin_session_id": _optional_text(owner.get("origin_session_id")),
            "origin_turn_id": _optional_text(owner.get("origin_turn_id")),
        }
        actual = {
            "authority": scope.authority.value,
            "principal_id": scope.principal_id,
            "channel": scope.channel,
            "conversation_key": scope.conversation_key,
            "origin_session_id": scope.origin_session_id,
            "origin_turn_id": scope.origin_turn_id,
        }
        if actual != expected:
            raise PermissionError("PrincipalScope does not match the durable session owner")


def _conversation_scope(
    *,
    channel: str,
    principal_key: str,
    conversation_key: str,
    session_id: str,
    issuer: object,
) -> PrincipalScope:
    if not principal_key:
        raise ValueError("principal_key is required")
    if not conversation_key:
        raise ValueError("conversation_key is required")
    return PrincipalScope(
        principal_id=build_conversation_key("principal", "conversation", channel, principal_key),
        authority=AuthorityKind.CONVERSATION,
        channel=channel,
        conversation_key=conversation_key,
        session_id=session_id,
        allowed_session_ids=frozenset({session_id}),
        _issuer=issuer,
        _factory=_HOST_AUTHORITY_FACTORY,
    )


def _operator_scope(
    *,
    active_session_id: str,
    owner_validated: bool,
    issuer: object,
    operator_authority: object,
) -> PrincipalScope:
    return PrincipalScope(
        principal_id=build_conversation_key("principal", "operator", "local"),
        authority=AuthorityKind.OPERATOR,
        channel=None,
        conversation_key=None,
        session_id=active_session_id,
        allowed_session_ids=frozenset({active_session_id}),
        _issuer=issuer,
        _owner_validated=owner_validated,
        _operator_authority=operator_authority,
        _factory=_HOST_AUTHORITY_FACTORY,
    )


def _scope_from_record(
    record: dict[str, Any],
    *,
    issuer: object,
    owner_validated: bool = False,
    operator_authority: object | None = None,
) -> PrincipalScope:
    return _PersistedScopeRecord.decode(record).to_scope(
        issuer=issuer,
        owner_validated=owner_validated,
        operator_authority=operator_authority,
    )


def _copy_scope(scope: PrincipalScope, *, owner_validated: bool) -> PrincipalScope:
    return PrincipalScope(
        principal_id=scope.principal_id,
        authority=scope.authority,
        channel=scope.channel,
        conversation_key=scope.conversation_key,
        session_id=scope.session_id,
        allowed_session_ids=scope.allowed_session_ids,
        origin_session_id=scope.origin_session_id,
        origin_turn_id=scope.origin_turn_id,
        _issuer=scope._issuer,
        _owner_validated=owner_validated,
        _operator_authority=scope._operator_authority,
        _factory=_HOST_AUTHORITY_FACTORY,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
