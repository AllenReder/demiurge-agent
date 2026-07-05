import pytest

from demiurge.channels.matrix.bridge import MatrixInteractionBridge
from demiurge.core import MatrixChannelConfig
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, InteractionRuntime


class FakeRunner:
    async def run_turn(self, *args, **kwargs):
        raise AssertionError("runner should not be called")


class FakeApi:
    def __init__(self):
        self.sent = []

    def send_message(self, *, room_id, body, txn_id):
        self.sent.append({"room_id": room_id, "body": body, "txn_id": txn_id})
        return {"event_id": "$event"}


def _bridge(config=None):
    return MatrixInteractionBridge(
        api=FakeApi(),
        config=config or MatrixChannelConfig(enabled=True, homeserver_url="https://matrix.example", access_token="token", user_id="@bot:example"),
        runtime=InteractionRuntime(FakeRunner()),
    )


def test_matrix_normalizes_sync_events_and_ignores_self():
    bridge = _bridge(MatrixChannelConfig(enabled=True, homeserver_url="https://matrix.example", access_token="token", user_id="@bot:example", allowed_rooms=["!room:example"]))

    inbound = bridge.normalize_sync(
        {
            "rooms": {
                "join": {
                    "!room:example": {
                        "timeline": {
                            "events": [
                                {"type": "m.room.message", "sender": "@bot:example", "content": {"msgtype": "m.text", "body": "ignore"}},
                                {"type": "m.room.message", "sender": "@alice:example", "event_id": "$1", "content": {"msgtype": "m.text", "body": "hello"}},
                            ]
                        }
                    }
                }
            }
        }
    )

    assert len(inbound) == 1
    assert inbound[0].channel == "matrix"
    assert inbound[0].text == "hello"
    assert inbound[0].source == "!room:example"
    assert inbound[0].conversation_key == "matrix:!room:example"


@pytest.mark.asyncio
async def test_matrix_deliver_sends_room_message(monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "_next_txn_id", lambda: "txn")

    await bridge.deliver(
        InteractionOutbound(
            "matrix",
            session_id="session_1",
            items=[InteractionItem.delivery_item(InteractionDelivery(text="hi"))],
            metadata={"source": "!room:example"},
        )
    )

    assert bridge.api.sent == [{"room_id": "!room:example", "body": "hi", "txn_id": "txn"}]
