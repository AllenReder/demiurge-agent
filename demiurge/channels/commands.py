from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from demiurge.runtime.interactions import InteractionInbound
from demiurge.slash import SlashCommand, parse_slash_command


CommandHandler = Callable[[str, InteractionInbound, Any], Awaitable[None]]
NoticeSender = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChannelCommandOutcome:
    handled: bool
    inbound: InteractionInbound
    command: SlashCommand | None = None


class ChannelCommandRuntime:
    """Command classification and dispatch for external channel adapters."""

    def __init__(
        self,
        *,
        command_names: set[str] | frozenset[str],
        unavailable_template: str,
        unknown_template: str,
    ) -> None:
        self.command_names = frozenset(command_names)
        self.unavailable_template = unavailable_template
        self.unknown_template = unknown_template

    async def handle(
        self,
        inbound: InteractionInbound,
        state: Any,
        *,
        handlers: Mapping[str, CommandHandler],
        send_notice: NoticeSender,
    ) -> ChannelCommandOutcome:
        command = parse_slash_command(inbound.text)
        if command is None:
            return ChannelCommandOutcome(handled=False, inbound=inbound)
        if command.name == "ask" and command.args:
            return ChannelCommandOutcome(
                handled=False,
                inbound=InteractionInbound(
                    channel=inbound.channel,
                    text=command.args,
                    source=inbound.source,
                    reply_to=inbound.reply_to,
                    conversation_key=inbound.conversation_key,
                    metadata=dict(inbound.metadata),
                ),
                command=command,
            )
        if command.name in self.command_names:
            handler = handlers.get(command.name)
            if handler is None:
                await send_notice(self.unavailable_template.format(name=command.name))
                return ChannelCommandOutcome(handled=True, inbound=inbound, command=command)
            await handler(command.args, inbound, state)
            return ChannelCommandOutcome(handled=True, inbound=inbound, command=command)
        await send_notice(self.unknown_template.format(name=command.name))
        return ChannelCommandOutcome(handled=True, inbound=inbound, command=command)
