from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

from demiurge.runtime.completions import is_background_completion
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
CompletionAfterEnqueue = Callable[[StateT, RuntimeTaskCompletionEvent, CompletionEnqueueResult], Awaitable[None]]
DrainPredicate = Callable[[StateT], bool]
TurnFinishedNotifier = Callable[[StateT, bool], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ConversationLifecycleConfig:
    channel: str
    merge_owner_id: str
    enqueue_owner_id: str
    require_source: bool = False
    fallback_source: str = ""


@dataclass(frozen=True, slots=True)
class ConversationSubmitResult:
    accepted: bool
    queued: bool
    busy_decision: BusyInboundDecision | None = None
    interrupted: bool = False


class _ConversationStateStore(Generic[StateT]):
    """Conversation-key state cache plus task-worker subscription ownership."""

    def __init__(
        self,
        *,
        state_factory: StateFactory[StateT],
        on_task_completion: Callable[[RuntimeTaskCompletionEvent], object],
    ) -> None:
        self._state_factory = state_factory
        self._on_task_completion = on_task_completion
        self._states: dict[str, StateT] = {}
        self._task_unsubscribes: dict[int, Callable[[], None]] = {}

    @property
    def states(self) -> dict[str, StateT]:
        return self._states

    def state_for_key(self, conversation_key: str) -> StateT:
        state = self._states.get(conversation_key)
        if state is not None:
            return state
        state = self._state_factory(conversation_key)
        self._states[conversation_key] = state
        self._subscribe_state_task_worker(state)
        return state

    def state_for_session(self, session_id: str) -> StateT | None:
        for state in self._states.values():
            if state.session_id == session_id:
                return state
        return None

    def close(self) -> None:
        for unsubscribe in list(self._task_unsubscribes.values()):
            unsubscribe()
        self._task_unsubscribes.clear()

    def _subscribe_state_task_worker(self, state: StateT) -> None:
        task_worker = self._task_worker_for_state(state)
        if task_worker is None:
            return
        worker_key = id(task_worker)
        if worker_key in self._task_unsubscribes:
            return
        self._task_unsubscribes[worker_key] = task_worker.subscribe(self._on_task_completion)

    @staticmethod
    def _task_worker_for_state(state: ConversationIngressState) -> Any:
        return getattr(getattr(state.runtime, "runner", None), "task_worker", None)


@dataclass(frozen=True, slots=True)
class _CompletionDeliveryRuntime:
    channel: str
    merge_owner_id: str
    enqueue_owner_id: str
    task_worker: Any = None
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
        after_completion_enqueue: CompletionAfterEnqueue[StateT] | None = None,
        should_drain: DrainPredicate[StateT] | None = None,
        after_turn: TurnFinishedNotifier[StateT] | None = None,
    ) -> None:
        self.config = config
        self._run_turn = run_turn
        self._notify_busy = notify_busy
        self._before_completion_enqueue = before_completion_enqueue
        self._after_completion_enqueue = after_completion_enqueue
        self._should_drain = should_drain
        self._after_turn = after_turn
        self._states = _ConversationStateStore(
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

    async def submit_inbound(
        self,
        state: StateT,
        inbound: InteractionInbound,
        *,
        fallback_source: str | None = None,
        wait_for_interruption: bool = False,
    ) -> ConversationSubmitResult:
        self.remember_route(state, inbound)
        if self.running(state):
            active_task = state.active_task
            decision = await self.handle_busy(state, inbound)
            interrupted = False
            if wait_for_interruption and decision.cancel_active and active_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await active_task
                interrupted = True
            return ConversationSubmitResult(
                accepted=True,
                queued=True,
                busy_decision=decision,
                interrupted=interrupted,
            )

        inbound = self.merge_pending(state, inbound, fallback_source=fallback_source)
        await self.accept_inbound(state, inbound)
        return ConversationSubmitResult(accepted=True, queued=False)

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
            drained = False
            if self._should_drain is None or self._should_drain(state):
                drained = await controller.drain_next(lambda next_inbound: self.run_state_turn(state, next_inbound))
            if self._after_turn is not None:
                await self._after_turn(state, drained)

    async def cancel_active(
        self,
        state: StateT,
        *,
        before_cancel: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        return await ConversationTurnController(state).cancel_active(before_cancel=before_cancel)

    @staticmethod
    def queued_count(state: ConversationIngressState) -> int:
        return state.queue.qsize()

    @staticmethod
    def clear_queue(state: ConversationIngressState, *, preserve_completions: bool) -> int:
        return ConversationTurnController(state).clear_queue(preserve_completions=preserve_completions)

    async def drain_next(self, state: StateT) -> bool:
        return await ConversationTurnController(state).drain_next(
            lambda next_inbound: self.run_state_turn(state, next_inbound)
        )

    async def queue_and_drain_if_idle(self, state: StateT, inbound: InteractionInbound) -> bool:
        return await ConversationTurnController(state).queue_and_drain_if_idle(
            inbound,
            lambda next_inbound: self.run_state_turn(state, next_inbound),
        )

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

        result = await self._completion_delivery(state).enqueue_event(
            state,
            event,
            run=lambda next_inbound: self.run_state_turn(state, next_inbound),
            before_enqueue=before_enqueue if self._before_completion_enqueue is not None else None,
        )
        if self._after_completion_enqueue is not None:
            await self._after_completion_enqueue(state, event, result)
        return result

    def _completion_delivery(self, state: StateT) -> _CompletionDeliveryRuntime:
        return _CompletionDeliveryRuntime(
            channel=self.config.channel,
            merge_owner_id=self.config.merge_owner_id,
            enqueue_owner_id=self.config.enqueue_owner_id,
            task_worker=state.task_worker,
            require_source=self.config.require_source,
            fallback_source=self.config.fallback_source,
        )
