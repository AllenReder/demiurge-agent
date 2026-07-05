from __future__ import annotations

import asyncio
import contextlib

from demiurge.runtime.completions import is_background_completion, merge_completion_inbounds
from demiurge.runtime.interactions import InteractionInbound


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
