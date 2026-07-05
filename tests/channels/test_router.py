import pytest

from demiurge.channels.base import ChannelRouterBridge
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound


class FakeBridge:
    def __init__(self):
        self.delivered = []

    async def deliver(self, outbound):
        self.delivered.append(outbound)

    async def prompt_user(self, prompt):
        return ""

    async def request_approval(self, request):
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_router_uses_fallback_for_missing_delivery_channel():
    built = []

    def fallback(channel):
        bridge = FakeBridge()
        built.append((channel, bridge))
        return bridge

    router = ChannelRouterBridge({}, fallback=fallback)
    outbound = InteractionOutbound(
        "email",
        session_id="session_1",
        items=[InteractionItem.delivery_item(InteractionDelivery(text="hi"))],
    )

    await router.deliver(outbound)

    assert built[0][0] == "email"
    assert built[0][1].delivered == [outbound]
