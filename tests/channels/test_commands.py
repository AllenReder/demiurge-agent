import pytest

from demiurge.channels.commands import ChannelCommandRuntime
from demiurge.runtime.interactions import InteractionInbound


def _runtime(**kwargs) -> ChannelCommandRuntime:
    return ChannelCommandRuntime(
        command_names=set(kwargs.pop("command_names", {"status"})),
        unavailable_template="Command not available: /{name}",
        unknown_template="Unknown command: /{name}",
    )


def _inbound(text: str) -> InteractionInbound:
    return InteractionInbound(
        channel="test",
        text=text,
        source="source_1",
        reply_to="reply_1",
        conversation_key="conversation_1",
        metadata={"kind": "test"},
    )


def _notice_sink(notices):
    async def send_notice(text: str) -> None:
        notices.append(text)

    return send_notice


@pytest.mark.asyncio
async def test_channel_command_runtime_passes_non_command_through():
    notices = []
    runtime = _runtime()

    outcome = await runtime.handle(_inbound("hello"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is False
    assert outcome.inbound.text == "hello"
    assert outcome.command is None
    assert notices == []


@pytest.mark.asyncio
async def test_channel_command_runtime_rewrites_ask_command_to_ordinary_inbound():
    notices = []
    runtime = _runtime()

    outcome = await runtime.handle(_inbound("/ask explain this"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is False
    assert outcome.inbound.text == "explain this"
    assert outcome.inbound.channel == "test"
    assert outcome.inbound.source == "source_1"
    assert outcome.inbound.reply_to == "reply_1"
    assert outcome.inbound.conversation_key == "conversation_1"
    assert outcome.inbound.metadata == {"kind": "test"}
    assert outcome.command is not None
    assert outcome.command.name == "ask"
    assert notices == []


@pytest.mark.asyncio
async def test_channel_command_runtime_dispatches_available_command_handler():
    calls = []
    notices = []
    state = object()

    async def status(args, inbound, handler_state):
        calls.append((args, inbound.text, handler_state is state))

    runtime = _runtime(command_names={"status"})

    outcome = await runtime.handle(
        _inbound("/status now"),
        state,
        handlers={"status": status},
        send_notice=_notice_sink(notices),
    )

    assert outcome.handled is True
    assert calls == [("now", "/status now", True)]
    assert notices == []


@pytest.mark.asyncio
async def test_channel_command_runtime_reports_available_command_without_handler():
    notices = []
    runtime = _runtime(command_names={"status"})

    outcome = await runtime.handle(_inbound("/status"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is True
    assert notices == ["Command not available: /status"]


@pytest.mark.asyncio
async def test_channel_command_runtime_reports_unknown_command():
    notices = []
    runtime = _runtime(command_names={"status"})

    outcome = await runtime.handle(_inbound("/missing"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is True
    assert notices == ["Unknown command: /missing"]
