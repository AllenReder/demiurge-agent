from __future__ import annotations

from types import SimpleNamespace

from demiurge.channels.base import TextChannelBridgeBase


class FakeTaskWorker:
    def __init__(self) -> None:
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)

        def unsubscribe() -> None:
            self.callbacks.remove(callback)

        return unsubscribe


class RecordingTextBridge(TextChannelBridgeBase):
    async def run_forever(self) -> None:
        return None

    async def _send_text(self, source: str, text: str, *, reply_to=None, metadata=None):
        return {"source": source, "text": text, "reply_to": reply_to, "metadata": metadata}


def _runtime(session_id: str, worker: FakeTaskWorker):
    runner = SimpleNamespace(session_id=session_id, task_worker=worker)
    return SimpleNamespace(runner=runner)


def test_text_bridge_subscribes_each_conversation_task_worker():
    worker_a = FakeTaskWorker()
    worker_b = FakeTaskWorker()
    workers = {"chat-a": worker_a, "chat-b": worker_b}

    bridge = RecordingTextBridge(
        channel_name="test",
        runtime_factory=lambda key: _runtime(f"session-{key}", workers[key]),
    )

    bridge._conversation_state("chat-a")
    bridge._conversation_state("chat-b")

    assert len(worker_a.callbacks) == 1
    assert len(worker_b.callbacks) == 1
