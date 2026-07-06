from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from demiurge.runtime.ingress import ConversationIngressState
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent


StateT = TypeVar("StateT", bound=ConversationIngressState)


class ConversationStateStore(Generic[StateT]):
    """Conversation-key state cache plus task-worker subscription ownership."""

    def __init__(
        self,
        *,
        state_factory: Callable[[str], StateT],
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
