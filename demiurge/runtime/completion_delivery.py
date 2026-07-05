from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from demiurge.runtime.completions import is_background_completion
from demiurge.runtime.ingress import CompletionEnqueueResult, ConversationIngressState, ConversationTurnController
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent, RuntimeTaskWorker


@dataclass(frozen=True, slots=True)
class CompletionDeliveryRuntime:
    channel: str
    merge_owner_id: str
    enqueue_owner_id: str
    task_worker: RuntimeTaskWorker | None = None
    require_source: bool = False
    fallback_source: str = ""

    def merge_pending_into(
        self,
        state: ConversationIngressState,
        inbound: InteractionInbound,
        *,
        fallback_source: str | None = None,
    ) -> InteractionInbound:
        if is_background_completion(inbound):
            return inbound
        return ConversationTurnController(state).merge_pending_completions(
            inbound,
            channel=self.channel,
            owner_id=self.merge_owner_id,
            fallback_source=self.fallback_source if fallback_source is None else fallback_source,
        )

    async def enqueue_event(
        self,
        state: ConversationIngressState,
        event: RuntimeTaskCompletionEvent,
        *,
        run: Callable[[InteractionInbound], Awaitable[None]],
        before_enqueue: Callable[[InteractionInbound], Awaitable[None]] | None = None,
    ) -> CompletionEnqueueResult:
        return await ConversationTurnController(state).enqueue_completion_event(
            event,
            channel=self.channel,
            owner_id=self.enqueue_owner_id,
            run=run,
            task_worker=self.task_worker,
            require_source=self.require_source,
            before_enqueue=before_enqueue,
        )
