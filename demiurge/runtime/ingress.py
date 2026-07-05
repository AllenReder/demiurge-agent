from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

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
