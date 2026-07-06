from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

from demiurge.runtime.completion_delivery import CompletionDeliveryRuntime
from demiurge.runtime.conversation_states import ConversationStateStore
from demiurge.runtime.ingress import (
    BusyInboundDecision,
    CompletionEnqueueResult,
    ConversationIngressState,
    ConversationTurnController,
)
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent


StateT = TypeVar("StateT", bound=ConversationIngressState)

StateFactory = Callable[[str], StateT]
ConversationTurnRunner = Callable[[StateT, InteractionInbound], Awaitable[None]]
BusyNotifier = Callable[[StateT, InteractionInbound, BusyInboundDecision], Awaitable[None]]
CompletionBeforeEnqueue = Callable[[StateT, RuntimeTaskCompletionEvent, InteractionInbound], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ConversationLifecycleConfig:
    channel: str
    merge_owner_id: str
    enqueue_owner_id: str
    require_source: bool = False
    fallback_source: str = ""


class ConversationLifecycleRuntime(Generic[StateT]):
    """Owns host conversation lifecycle for channel adapters."""

    def __init__(
        self,
        *,
        config: ConversationLifecycleConfig,
        state_factory: StateFactory[StateT],
        run_turn: ConversationTurnRunner[StateT],
        notify_busy: BusyNotifier[StateT] | None = None,
        before_completion_enqueue: CompletionBeforeEnqueue[StateT] | None = None,
    ) -> None:
        self.config = config
        self._run_turn = run_turn
        self._notify_busy = notify_busy
        self._before_completion_enqueue = before_completion_enqueue
        self._states = ConversationStateStore(
            state_factory=state_factory,
            on_task_completion=self._on_task_completion,
        )

    @property
    def states(self) -> dict[str, StateT]:
        return self._states.states

    def state_for_key(self, conversation_key: str) -> StateT:
        return self._states.state_for_key(conversation_key)

    def state_for_session(self, session_id: str) -> StateT | None:
        return self._states.state_for_session(session_id)

    def close(self) -> None:
        self._states.close()

    @staticmethod
    def running(state: ConversationIngressState) -> bool:
        return ConversationTurnController(state).running

    @staticmethod
    def remember_route(state: ConversationIngressState, inbound: InteractionInbound) -> None:
        state.remember_route(inbound)

    def merge_pending(
        self,
        state: StateT,
        inbound: InteractionInbound,
        *,
        fallback_source: str | None = None,
    ) -> InteractionInbound:
        return self._completion_delivery(state).merge_pending_into(
            state,
            inbound,
            fallback_source=fallback_source,
        )

    async def accept_inbound(self, state: StateT, inbound: InteractionInbound) -> bool:
        if self.running(state):
            await self.handle_busy(state, inbound)
            return False
        self.start_turn(state, inbound)
        return True

    async def handle_busy(self, state: StateT, inbound: InteractionInbound) -> BusyInboundDecision:
        async def notify(decision: BusyInboundDecision) -> None:
            if self._notify_busy is not None:
                await self._notify_busy(state, inbound, decision)

        return await ConversationTurnController(state).handle_busy_inbound(inbound, notify=notify)

    def start_turn(self, state: StateT, inbound: InteractionInbound) -> bool:
        return ConversationTurnController(state).start(
            inbound,
            lambda next_inbound: self.run_state_turn(state, next_inbound),
        )

    async def run_state_turn(self, state: StateT, inbound: InteractionInbound) -> None:
        task = asyncio.current_task()
        try:
            await self._run_turn(state, inbound)
        finally:
            controller = ConversationTurnController(state)
            controller.finish(task)
            await controller.drain_next(lambda next_inbound: self.run_state_turn(state, next_inbound))

    async def cancel_active(
        self,
        state: StateT,
        *,
        before_cancel: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        return await ConversationTurnController(state).cancel_active(before_cancel=before_cancel)

    def _on_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        state = self.state_for_session(event.owner_session_id)
        if state is None:
            return
        try:
            asyncio.get_running_loop().create_task(self._enqueue_task_completion(state, event))
        except RuntimeError:
            return

    async def _enqueue_task_completion(self, state: StateT, event: RuntimeTaskCompletionEvent) -> CompletionEnqueueResult:
        async def before_enqueue(inbound: InteractionInbound) -> None:
            if self._before_completion_enqueue is not None:
                await self._before_completion_enqueue(state, event, inbound)

        return await self._completion_delivery(state).enqueue_event(
            state,
            event,
            run=lambda next_inbound: self.run_state_turn(state, next_inbound),
            before_enqueue=before_enqueue if self._before_completion_enqueue is not None else None,
        )

    def _completion_delivery(self, state: StateT) -> CompletionDeliveryRuntime:
        return CompletionDeliveryRuntime(
            channel=self.config.channel,
            merge_owner_id=self.config.merge_owner_id,
            enqueue_owner_id=self.config.enqueue_owner_id,
            task_worker=state.task_worker,
            require_source=self.config.require_source,
            fallback_source=self.config.fallback_source,
        )
