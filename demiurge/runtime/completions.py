from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.scope import PrincipalScope, PrincipalScopeResolver
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent, RuntimeTaskWorker


BACKGROUND_COMPLETION_TRIGGER = "background_task"


@dataclass(frozen=True, slots=True)
class CompletionRoute:
    channel: str
    source: str
    principal_key: str | None = None
    reply_to: str | None = None
    conversation_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class CompletionInbox:
    """Host-owned background completion intake for interaction adapters."""

    def __init__(self, task_worker: RuntimeTaskWorker):
        self.task_worker = task_worker

    def inbound_for_event(
        self,
        event: RuntimeTaskCompletionEvent,
        *,
        route: CompletionRoute,
        claim_id: str | None = None,
        principal_scope: PrincipalScope | None = None,
    ) -> InteractionInbound:
        event_metadata = event.to_metadata()
        if claim_id is not None:
            event_metadata["completion_claim_id"] = claim_id
        return InteractionInbound(
            channel=(principal_scope.channel if principal_scope is not None else None) or route.channel,
            text=event.to_inbound_text(),
            source=route.source,
            principal_key=None if principal_scope is not None else route.principal_key,
            reply_to=route.reply_to,
            conversation_key=(
                principal_scope.conversation_key if principal_scope is not None else None
            )
            or route.conversation_key,
            metadata={**dict(route.metadata or {}), **event_metadata},
            principal_scope=principal_scope,
        )

    def claim_event(
        self,
        event: RuntimeTaskCompletionEvent,
        *,
        owner_id: str,
        route: CompletionRoute,
    ) -> InteractionInbound | None:
        principal_scope = self._scope_for_event(event)
        claim = self.task_worker.claim_pending_event(event.event_id, owner_id=owner_id)
        if claim is None:
            return None
        return self.inbound_for_event(
            event,
            route=route,
            claim_id=claim.claim_id,
            principal_scope=principal_scope,
        )

    def claim_pending_for_session(
        self,
        session_id: str,
        *,
        owner_id: str,
        route: CompletionRoute,
    ) -> list[InteractionInbound]:
        completions: list[InteractionInbound] = []
        for event in self.task_worker.pending_events_for_session(session_id):
            inbound = self.claim_event(event, owner_id=owner_id, route=route)
            if inbound is not None:
                completions.append(inbound)
        return completions

    def ack_from_metadata(self, metadata: Mapping[str, Any]) -> int:
        acknowledged = 0
        for event_id, claim_id in completion_claims_from_metadata(metadata):
            if self.task_worker.ack_pending_event_id(event_id, claim_id=claim_id):
                acknowledged += 1
        return acknowledged

    def _scope_for_event(self, event: RuntimeTaskCompletionEvent) -> PrincipalScope | None:
        record = event.origin_scope_record
        control_plane = getattr(self.task_worker, "control_plane", None)
        if record is None:
            if control_plane is not None:
                raise PermissionError(
                    "background completion has no persisted origin PrincipalScope"
                )
            return None
        if control_plane is None:
            raise RuntimeError("scoped completion requires a durable RuntimeTaskWorker")
        scope = PrincipalScopeResolver(control_plane.store).background_completion(
            origin_record=dict(record),
            owner_session_id=event.owner_session_id,
        )
        if scope.session_id != event.owner_session_id:
            raise PermissionError("background completion scope does not match owner session")
        return scope


def is_background_completion(inbound: InteractionInbound) -> bool:
    return inbound.metadata.get("trigger") == BACKGROUND_COMPLETION_TRIGGER


def merge_completion_inbounds(user_inbound: InteractionInbound, completions: list[InteractionInbound]) -> InteractionInbound:
    metadata = dict(user_inbound.metadata)
    metadata["merged_background_tasks"] = [
        item.metadata.get("task_id") for item in completions if item.metadata.get("task_id")
    ]
    metadata["completion_claims"] = [
        {"event_id": item.metadata.get("event_id"), "claim_id": item.metadata.get("completion_claim_id")}
        for item in completions
        if item.metadata.get("event_id") and item.metadata.get("completion_claim_id")
    ]
    completion_text = "\n\n".join(item.text for item in completions if item.text)
    text = "\n\n".join(
        part
        for part in [
            user_inbound.text,
            "[SYSTEM: Pending background task events merged into this user turn]",
            completion_text,
        ]
        if part
    )
    return InteractionInbound(
        channel=user_inbound.channel,
        text=text,
        source=user_inbound.source,
        principal_key=user_inbound.principal_key,
        reply_to=user_inbound.reply_to,
        conversation_key=user_inbound.conversation_key,
        metadata=metadata,
        attachments=list(user_inbound.attachments),
        principal_scope=user_inbound.principal_scope,
    )


def completion_claims_from_metadata(metadata: Mapping[str, Any]) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    raw_claims = metadata.get("completion_claims")
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, Mapping):
                continue
            event_id = item.get("event_id")
            claim_id = item.get("claim_id")
            if event_id and claim_id:
                claims.append((str(event_id), str(claim_id)))
    event_id = metadata.get("event_id")
    claim_id = metadata.get("completion_claim_id")
    if event_id and claim_id:
        claims.append((str(event_id), str(claim_id)))
    return claims
