from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from demiurge.runtime.completions import (
    CompletionInbox,
    CompletionRoute,
    is_background_completion,
    merge_completion_inbounds,
)
from demiurge.runtime.interactions import InteractionInbound, SessionRouteBinding


class InboundQueueRuntime:
    """Queue rules for inbound user turns and background completion turns."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[InteractionInbound] = asyncio.Queue()

    async def put(self, inbound: InteractionInbound) -> None:
        await self._queue.put(inbound)

    def put_nowait(self, inbound: InteractionInbound) -> None:
        self._queue.put_nowait(inbound)

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()

    def clear(self, *, preserve_completions: bool) -> int:
        count = 0
        preserved: list[InteractionInbound] = []
        for inbound in self._drain_nowait():
            if preserve_completions and is_background_completion(inbound):
                preserved.append(inbound)
                continue
            count += 1
        self._restore(preserved)
        return count

    def next_inbound(self) -> InteractionInbound:
        pending = self._drain_nowait()
        if not pending:
            raise asyncio.QueueEmpty
        selected_index = self._first_user_index(pending)
        selected = pending.pop(selected_index)
        if not is_background_completion(selected):
            completions = [item for item in pending if is_background_completion(item)]
            pending = [item for item in pending if not is_background_completion(item)]
            if completions:
                selected = merge_completion_inbounds(selected, completions)
        self._restore(pending)
        return selected

    def merge_completions_into(
        self,
        inbound: InteractionInbound,
        *,
        stored_completions: list[InteractionInbound] | None = None,
    ) -> InteractionInbound:
        completions = list(stored_completions or [])
        pending = self._drain_nowait()
        completions.extend(item for item in pending if is_background_completion(item))
        self._restore([item for item in pending if not is_background_completion(item)])
        if not completions:
            return inbound
        return merge_completion_inbounds(inbound, completions)

    def _drain_nowait(self) -> list[InteractionInbound]:
        pending: list[InteractionInbound] = []
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                pending.append(self._queue.get_nowait())
        return pending

    def _restore(self, pending: list[InteractionInbound]) -> None:
        for inbound in pending:
            self._queue.put_nowait(inbound)

    @staticmethod
    def _first_user_index(pending: list[InteractionInbound]) -> int:
        user_index = next((index for index, item in enumerate(pending) if not is_background_completion(item)), None)
        return user_index if user_index is not None else 0


@dataclass(slots=True)
class ConversationIngressState:
    runtime: Any
    busy_mode: str
    route_binding: SessionRouteBinding
    conversation_key: str = ""
    source: str = ""
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    active_task: asyncio.Task[None] | None = None
    queue: InboundQueueRuntime = field(default_factory=InboundQueueRuntime)

    @property
    def runner(self) -> Any:
        return getattr(self.runtime, "runner", None)

    @property
    def session_id(self) -> str | None:
        session_id = getattr(self.runner, "session_id", None)
        return str(session_id) if session_id else None

    @property
    def task_worker(self) -> Any:
        return getattr(self.runner, "task_worker", None)

    def remember_route(self, inbound: InteractionInbound) -> None:
        self.source = inbound.source
        self.reply_to = inbound.reply_to
        self.metadata = dict(inbound.metadata)
        self.conversation_key = inbound.conversation_key or self.conversation_key

    def completion_route(self, channel: str, *, fallback_source: str = "") -> CompletionRoute:
        return CompletionRoute(
            channel=channel,
            source=self.source or fallback_source,
            reply_to=self.reply_to,
            conversation_key=self.conversation_key,
            metadata=self.metadata,
        )

    def claim_pending_completions(
        self,
        *,
        channel: str,
        owner_id: str,
        fallback_source: str = "",
    ) -> list[InteractionInbound]:
        task_worker = self.task_worker
        session_id = self.session_id
        if task_worker is None or session_id is None:
            return []
        return CompletionInbox(task_worker).claim_pending_for_session(
            session_id,
            owner_id=owner_id,
            route=self.completion_route(channel, fallback_source=fallback_source),
        )

    def claim_completion_event(
        self,
        event: Any,
        *,
        channel: str,
        owner_id: str,
        task_worker: Any = None,
    ) -> InteractionInbound | None:
        worker = task_worker if task_worker is not None else self.task_worker
        if worker is None:
            return None
        return CompletionInbox(worker).claim_event(
            event,
            owner_id=owner_id,
            route=self.completion_route(channel),
        )


@dataclass(frozen=True, slots=True)
class BusyInboundDecision:
    kind: str
    notify: bool
    cancel_active: bool


class ConversationTurnController:
    """Turn lifecycle rules for one conversation ingress state."""

    def __init__(self, state: ConversationIngressState):
        self.state = state

    @property
    def running(self) -> bool:
        task = self.state.active_task
        return bool(task and not task.done())

    async def handle_busy_inbound(
        self,
        inbound: InteractionInbound,
        *,
        notify: Callable[[BusyInboundDecision], Awaitable[None]] | None = None,
    ) -> BusyInboundDecision:
        await self.state.queue.put(inbound)
        if is_background_completion(inbound):
            return BusyInboundDecision(kind="background_completion", notify=False, cancel_active=False)
        if self.state.busy_mode == "queue":
            decision = BusyInboundDecision(kind="queue", notify=True, cancel_active=False)
            if notify is not None:
                await notify(decision)
            return decision
        decision = BusyInboundDecision(kind="interrupt", notify=True, cancel_active=True)
        if notify is not None:
            await notify(decision)
        task = self.state.active_task
        if task and not task.done():
            task.cancel()
        return decision

    def start(
        self,
        inbound: InteractionInbound,
        run: Callable[[InteractionInbound], Awaitable[None]],
    ) -> bool:
        if self.running:
            self.state.queue.put_nowait(inbound)
            return False
        self.state.active_task = asyncio.create_task(run(inbound))
        return True

    def finish(self, task: asyncio.Task[Any] | None) -> bool:
        if self.state.active_task is not task:
            return False
        self.state.active_task = None
        return True

    async def drain_next(
        self,
        run: Callable[[InteractionInbound], Awaitable[None]],
    ) -> bool:
        if self.running or self.state.queue.empty():
            return False
        return self.start(self.state.queue.next_inbound(), run)

    async def cancel_active(
        self,
        *,
        before_cancel: Callable[[], Awaitable[None]] | None = None,
        timeout_seconds: float = 5,
    ) -> bool:
        task = self.state.active_task
        if not task or task.done():
            return False
        if before_cancel is not None:
            await before_cancel()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        return True

    def clear_queue(self, *, preserve_completions: bool) -> int:
        return self.state.queue.clear(preserve_completions=preserve_completions)

    def next_queued_input(self) -> InteractionInbound:
        return self.state.queue.next_inbound()

    async def queue_and_drain_if_idle(
        self,
        inbound: InteractionInbound,
        run: Callable[[InteractionInbound], Awaitable[None]],
    ) -> bool:
        await self.state.queue.put(inbound)
        if self.running:
            return False
        return await self.drain_next(run)

    async def enqueue_completion(
        self,
        inbound: InteractionInbound,
        run: Callable[[InteractionInbound], Awaitable[None]],
    ) -> str:
        if self.running:
            await self.state.queue.put(inbound)
            return "queued_running"
        if not self.state.queue.empty():
            await self.state.queue.put(inbound)
            await self.drain_next(run)
            return "queued_drained"
        self.start(inbound, run)
        return "started"
