from __future__ import annotations

from types import SimpleNamespace

import pytest

from demiurge.channels.base import TextChannelBridgeBase
from demiurge.runtime.interactions import InteractionInbound


class FakeTaskWorker:
    def __init__(self) -> None:
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)

        def unsubscribe() -> None:
            self.callbacks.remove(callback)

        return unsubscribe


class RecordingTextBridge(TextChannelBridgeBase):
    def __init__(self, **kwargs):
        self.sent = []
        super().__init__(**kwargs)

    async def run_forever(self) -> None:
        return None

    async def _send_text(self, source: str, text: str, *, reply_to=None, metadata=None):
        sent = {"source": source, "text": text, "reply_to": reply_to, "metadata": metadata}
        self.sent.append(sent)
        return sent


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


@pytest.mark.asyncio
async def test_text_bridge_redacts_channel_exception_before_log_and_reply(caplog):
    worker = FakeTaskWorker()
    secret = "SYNTHETIC_CHANNEL_EXCEPTION_SECRET"

    class FailingRuntime:
        runner = SimpleNamespace(session_id="session-failing", task_worker=worker)

        async def handle(self, _inbound, *, route_binding):
            raise RuntimeError(
                f"Authorization: Bearer {secret}"
            )

    bridge = RecordingTextBridge(
        channel_name="test",
        runtime_factory=lambda _key: FailingRuntime(),
    )
    state = bridge._conversation_state("chat-failing")
    inbound = InteractionInbound(
        channel="test",
        text="hello",
        source="user-1",
        conversation_key="chat-failing",
    )

    await bridge._run_inbound(state, inbound)

    assert secret not in caplog.text
    assert secret not in bridge.sent[-1]["text"]
    assert "<redacted:AUTHORIZATION>" in bridge.sent[-1]["text"]


@pytest.mark.asyncio
async def test_text_bridge_uses_known_provider_secret_for_unstructured_exception(caplog):
    worker = FakeTaskWorker()
    secret = "SYNTHETIC_UNSTRUCTURED_PROVIDER_SECRET"

    class FailingRuntime:
        runner = SimpleNamespace(
            session_id="session-failing",
            task_worker=worker,
            provider=SimpleNamespace(api_key=secret),
        )

        async def handle(self, _inbound, *, route_binding):
            raise RuntimeError(f"upstream rejected credential {secret}")

    bridge = RecordingTextBridge(
        channel_name="test",
        runtime_factory=lambda _key: FailingRuntime(),
    )
    state = bridge._conversation_state("chat-failing")
    inbound = InteractionInbound(
        channel="test",
        text="hello",
        source="user-1",
        conversation_key="chat-failing",
    )

    await bridge._run_inbound(state, inbound)

    assert secret not in caplog.text
    assert secret not in bridge.sent[-1]["text"]
    assert "<redacted:API_KEY>" in bridge.sent[-1]["text"]
