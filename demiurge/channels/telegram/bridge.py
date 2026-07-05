from __future__ import annotations

import asyncio
import contextlib
import contextvars
import http.client
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from demiurge.channels.commands import ChannelCommandRuntime
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.security.capabilities import CapabilityFacade
from demiurge.core import TelegramChannelConfig
from demiurge.runtime.completions import is_background_completion
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent, RuntimeTaskWorker
from demiurge.runtime.delegation import subagents_command_text
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionOutbound,
    InteractionRuntime,
    SessionRouteBinding,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.ingress import ConversationIngressState, ConversationTurnController
from demiurge.runtime.session_commands import build_session_list_view, resolve_session_choice
from demiurge.runtime.status_commands import build_runtime_status_view, format_runtime_status_markdown
from demiurge.providers import ToolCall
from demiurge.sdk import AgentInput, TurnContext
from demiurge.slash import command_names_for_surface, help_text_for_surface, parse_slash_command, telegram_command_specs
from demiurge.channels.telegram.bot_api import TelegramApiError, TelegramBotApi
from demiurge.channels.telegram.formatting import (
    _needs_rich_telegram_rendering,
    _strip_mdv2,
    _telegram_message_id,
    format_telegram_markdown_v2,
    split_telegram_message,
)


logger = logging.getLogger(__name__)




@dataclass(slots=True)
class TelegramConversationState(ConversationIngressState):
    pending_approval_id: str | None = None


@dataclass(slots=True)
class TelegramPendingApproval:
    request: ApprovalRequest
    future: asyncio.Future[ApprovalDecision]
    source: str
    reply_to: str | None
    conversation_key: str
    message_id: int | None = None


TELEGRAM_APPROVAL_TIMEOUT_SECONDS = 600
TELEGRAM_POLL_NETWORK_BASE_DELAY_SECONDS = 1.0
TELEGRAM_POLL_NETWORK_MAX_DELAY_SECONDS = 30.0
TELEGRAM_POLL_CONFLICT_BASE_DELAY_SECONDS = 15.0
TELEGRAM_POLL_CONFLICT_STEP_DELAY_SECONDS = 10.0
TELEGRAM_POLL_CONFLICT_MAX_RETRIES = 5


def _normalize_tool_display(value: str | None) -> str:
    normalized = (value or "summary").strip().lower()
    return normalized if normalized in {"quiet", "summary", "full"} else "summary"


def _tool_call_start_summary(call: ToolCall) -> str:
    if call.name == "terminal":
        command = str(call.arguments.get("command") or "").strip()
        return f"$ {command}" if command else "running terminal"
    if call.name in {"read_file", "write_file", "patch"}:
        path = call.arguments.get("path") or call.arguments.get("file_path")
        return f"{call.name}: {path}" if path else call.name
    if call.arguments:
        return json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    return "running"


























class TelegramInteractionBridge:
    def __init__(
        self,
        *,
        api: TelegramBotApi,
        runtime: InteractionRuntime | None = None,
        runtime_factory: Callable[[str], InteractionRuntime] | None = None,
        bot_username: str | None = None,
        poll_timeout: int = 30,
        message_format: str = "markdown_v2",
        busy_mode: str = "interrupt",
        register_commands: bool = True,
        send_typing: bool = True,
        rich_messages: bool = True,
        reply_to_mode: str = "off",
        allowed_users: list[int] | None = None,
        allowed_chats: list[int] | None = None,
        unauthorized_response: str = "brief",
        approval_timeout_seconds: float = TELEGRAM_APPROVAL_TIMEOUT_SECONDS,
        tool_display: str = "summary",
    ):
        if runtime is None and runtime_factory is None:
            raise ValueError("TelegramInteractionBridge requires runtime or runtime_factory")
        self.api = api
        self._runtime_factory = runtime_factory or (lambda _conversation_key: runtime)  # type: ignore[return-value]
        self.bot_username = bot_username.lstrip("@") if bot_username else None
        self.poll_timeout = poll_timeout
        self.message_format = message_format
        self.default_busy_mode = busy_mode
        self.register_commands_enabled = register_commands
        self.send_typing = send_typing
        self.rich_messages = rich_messages
        self.reply_to_mode = reply_to_mode
        self.allowed_users = set(allowed_users or [])
        self.allowed_chats = set(allowed_chats or [])
        self.unauthorized_response = unauthorized_response
        self.approval_timeout_seconds = approval_timeout_seconds
        self.tool_display = _normalize_tool_display(tool_display)
        self._command_runtime = ChannelCommandRuntime(
            command_names=command_names_for_surface("telegram"),
            unavailable_template="Command not available on Telegram: /{name}",
            unknown_template="Unknown command: /{name}",
        )
        self._polling_network_error_count = 0
        self._polling_conflict_count = 0
        self._polling_network_base_delay = TELEGRAM_POLL_NETWORK_BASE_DELAY_SECONDS
        self._polling_network_max_delay = TELEGRAM_POLL_NETWORK_MAX_DELAY_SECONDS
        self._polling_conflict_base_delay = TELEGRAM_POLL_CONFLICT_BASE_DELAY_SECONDS
        self._polling_conflict_step_delay = TELEGRAM_POLL_CONFLICT_STEP_DELAY_SECONDS
        self._polling_conflict_max_retries = TELEGRAM_POLL_CONFLICT_MAX_RETRIES
        self._rich_messages_disabled = False
        self.offset: int | None = None
        self._pending_choices: dict[str, list[str]] = {}
        self._pending_approvals: dict[str, TelegramPendingApproval] = {}
        self._approval_counter = 0
        self._active_inbound: contextvars.ContextVar[InteractionInbound | None] = contextvars.ContextVar(
            "demiurge_telegram_active_inbound",
            default=None,
        )
        self._conversations: dict[str, TelegramConversationState] = {}
        self._tool_message_ids: dict[tuple[str, str], tuple[str, int]] = {}
        self._task_worker: RuntimeTaskWorker | None = None
        self._task_unsubscribe: Callable[[], None] | None = None

    @classmethod
    def from_config(
        cls,
        runtime: InteractionRuntime | None,
        config: TelegramChannelConfig,
        *,
        runtime_factory: Callable[[str], InteractionRuntime] | None = None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
    ) -> "TelegramInteractionBridge":
        token = _resolve_telegram_token(config)
        if not token:
            raise RuntimeError("telegram channel requires bot_token_env with a value or bot_token")
        return cls(
            runtime=runtime,
            runtime_factory=runtime_factory,
            api=TelegramBotApi(token),
            bot_username=config.bot_username,
            poll_timeout=config.poll_timeout,
            message_format=config.message_format,
            busy_mode=busy_mode,
            register_commands=config.register_commands,
            send_typing=config.send_typing,
            rich_messages=config.rich_messages,
            reply_to_mode=config.reply_to_mode,
            allowed_users=list(config.allowed_users),
            allowed_chats=list(config.allowed_chats),
            unauthorized_response=config.unauthorized_response,
            tool_display=tool_display,
        )

    async def run_forever(self) -> None:
        await self.clear_webhook()
        await self.register_commands()
        while True:
            try:
                updates = await asyncio.to_thread(self.api.get_updates, offset=self.offset, timeout=self.poll_timeout)
            except TelegramApiError as exc:
                if await self._handle_polling_api_error(exc):
                    continue
                raise
            except Exception as exc:
                if _is_transient_telegram_transport_error(exc):
                    await self._handle_polling_network_error(exc)
                    continue
                raise
            self._reset_polling_error_counts()
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self.offset = update_id + 1
                await self.handle_update(update)
            if not updates:
                await asyncio.sleep(0.2)

    async def clear_webhook(self) -> None:
        if not hasattr(self.api, "delete_webhook"):
            return
        try:
            await asyncio.to_thread(self.api.delete_webhook, drop_pending_updates=False)
        except Exception as exc:
            logger.warning("telegram deleteWebhook failed before polling startup: %s", exc)

    async def register_commands(self) -> None:
        if not self.register_commands_enabled or not hasattr(self.api, "set_my_commands"):
            return
        commands = [{"command": spec.name, "description": spec.description[:256]} for spec in telegram_command_specs()]
        try:
            await asyncio.to_thread(self.api.set_my_commands, commands)
        except Exception as exc:
            logger.warning("telegram setMyCommands failed: %s", exc)

    async def _handle_polling_api_error(self, exc: TelegramApiError) -> bool:
        if exc.error_code == 409:
            self._polling_conflict_count += 1
            if self._polling_conflict_count > self._polling_conflict_max_retries:
                raise RuntimeError(
                    "telegram polling conflict did not clear; another gateway may be using this bot token"
                ) from exc
            delay = self._polling_conflict_delay()
            logger.warning(
                "telegram getUpdates conflict (%d/%d), retrying in %.1fs: %s",
                self._polling_conflict_count,
                self._polling_conflict_max_retries,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
            return True

        retry_after = exc.retry_after
        if exc.error_code == 429 and retry_after is not None:
            delay = max(0.0, retry_after)
            logger.warning("telegram getUpdates rate limited, retrying in %.1fs: %s", delay, exc)
            await asyncio.sleep(delay)
            return True

        return False

    async def _handle_polling_network_error(self, exc: Exception) -> None:
        self._polling_network_error_count += 1
        delay = min(
            self._polling_network_base_delay * (2 ** max(0, self._polling_network_error_count - 1)),
            self._polling_network_max_delay,
        )
        logger.warning(
            "telegram getUpdates transient network error (%d), retrying in %.1fs: %s",
            self._polling_network_error_count,
            delay,
            exc,
        )
        await asyncio.sleep(delay)

    def _polling_conflict_delay(self) -> float:
        return self._polling_conflict_base_delay + (
            max(0, self._polling_conflict_count - 1) * self._polling_conflict_step_delay
        )

    def _reset_polling_error_counts(self) -> None:
        self._polling_network_error_count = 0
        self._polling_conflict_count = 0

    async def handle_update(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            if not await self._authorize_callback(callback):
                return
            if str(callback.get("data") or "").startswith("approval:"):
                await self._handle_approval_callback(callback)
                return
        inbound = self.normalize_update(update)
        callback_query_id = inbound.metadata.get("telegram_callback_query_id") if inbound else None
        if callback_query_id:
            await self._answer_callback_query(str(callback_query_id))
        if inbound is None:
            return
        if not await self._authorize_inbound(inbound):
            return
        inbound = self._consume_inbound_pending_choice(inbound)
        await self.handle_inbound(inbound)

    def normalize_update(self, update: dict[str, Any]) -> InteractionInbound | None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            return self._normalize_callback_query(update, callback)
        message = update.get("message") or {}
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None
        chat_type = chat.get("type") or "private"
        user_id = (message.get("from") or {}).get("id")
        message_id = message.get("message_id")
        normalized = self._normalize_text_for_chat(text, chat_type=chat_type, message=message)
        if normalized is None or not normalized.strip():
            return None
        conversation_key = f"telegram:{chat_id}"
        if self._telegram_access_allowed(chat_id=chat_id, chat_type=str(chat_type), user_id=user_id):
            normalized = self._consume_pending_choice(conversation_key, normalized.strip())
        return InteractionInbound(
            channel="telegram",
            text=normalized.strip(),
            source=str(chat_id),
            reply_to=str(message_id) if message_id is not None else None,
            conversation_key=conversation_key,
            metadata={
                "telegram_chat_id": chat_id,
                "telegram_chat_type": chat_type,
                "telegram_user_id": user_id,
                "telegram_update_id": update.get("update_id"),
            },
        )

    def _consume_inbound_pending_choice(self, inbound: InteractionInbound) -> InteractionInbound:
        if not inbound.conversation_key:
            return inbound
        text = self._consume_pending_choice(inbound.conversation_key, inbound.text.strip())
        if text == inbound.text:
            return inbound
        return InteractionInbound(
            channel=inbound.channel,
            text=text,
            source=inbound.source,
            reply_to=inbound.reply_to,
            conversation_key=inbound.conversation_key,
            metadata=dict(inbound.metadata),
        )

    async def _authorize_callback(self, callback: dict[str, Any]) -> bool:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = str(chat.get("type") or "private")
        user_id = (callback.get("from") or {}).get("id")
        allowed = self._telegram_access_allowed(chat_id=chat_id, chat_type=chat_type, user_id=user_id)
        if allowed:
            return True
        callback_id = callback.get("id")
        if callback_id:
            await self._answer_callback_query(str(callback_id), text="Telegram access denied.")
        return False

    async def _authorize_inbound(self, inbound: InteractionInbound) -> bool:
        if self._inbound_authorized(inbound):
            return True
        if self.unauthorized_response == "brief":
            await self._send_text(
                inbound.source,
                "Telegram access denied for this user or chat.",
                reply_to=inbound.reply_to,
            )
        return False

    def _inbound_authorized(self, inbound: InteractionInbound) -> bool:
        return self._telegram_access_allowed(
            chat_id=inbound.metadata.get("telegram_chat_id"),
            chat_type=str(inbound.metadata.get("telegram_chat_type") or "private"),
            user_id=inbound.metadata.get("telegram_user_id"),
        )

    def _telegram_access_allowed(self, *, chat_id: Any, chat_type: str, user_id: Any) -> bool:
        try:
            user = int(user_id)
        except (TypeError, ValueError):
            return False
        if chat_type == "private":
            return user in self.allowed_users
        try:
            chat = int(chat_id)
        except (TypeError, ValueError):
            return False
        return user in self.allowed_users and chat in self.allowed_chats

    def _normalize_callback_query(self, update: dict[str, Any], callback: dict[str, Any]) -> InteractionInbound | None:
        data = str(callback.get("data") or "")
        if not data.startswith("choice:"):
            return None
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None
        chat_type = chat.get("type") or "private"
        user_id = (callback.get("from") or {}).get("id")
        conversation_key = f"telegram:{chat_id}"
        choices = self._pending_choices.pop(conversation_key, None)
        if not choices:
            return None
        try:
            index = int(data.split(":", 1)[1])
        except ValueError:
            return None
        if index < 0 or index >= len(choices):
            return None
        message_id = message.get("message_id")
        return InteractionInbound(
            channel="telegram",
            text=choices[index],
            source=str(chat_id),
            reply_to=str(message_id) if message_id is not None else None,
            conversation_key=conversation_key,
            metadata={
                "telegram_update_id": update.get("update_id"),
                "telegram_callback_query_id": callback.get("id"),
                "telegram_callback_data": data,
                "telegram_chat_id": chat_id,
                "telegram_chat_type": chat_type,
                "telegram_user_id": user_id,
            },
        )

    async def handle_inbound(self, inbound: InteractionInbound) -> None:
        state = self._conversation_state(inbound.conversation_key or f"telegram:{inbound.source}")
        self._remember_route(state, inbound)
        command_outcome = await self._handle_telegram_command(inbound, state)
        if command_outcome.handled:
            return
        inbound = command_outcome.inbound

        if not is_background_completion(inbound):
            inbound = self._merge_stored_task_completions(state, inbound)
        if state.active_task and not state.active_task.done():
            await self._handle_busy_inbound(state, inbound)
            return
        self._start_turn(state, inbound)

    async def deliver(self, outbound: InteractionOutbound) -> None:
        try:
            pending_tool_results = []
            for item in outbound.items:
                if item.kind == "tool_call" and item.tool_call is not None:
                    if pending_tool_results:
                        await self._deliver_tool_results(pending_tool_results, outbound=outbound)
                        pending_tool_results = []
                    await self._deliver_tool_call(item.tool_call, outbound=outbound)
                    continue
                if item.kind == "tool_result" and item.tool_result is not None:
                    pending_tool_results.append(item.tool_result)
                    continue
                if item.kind == "delivery" and item.delivery is not None:
                    if pending_tool_results:
                        await self._deliver_tool_results(pending_tool_results, outbound=outbound)
                        pending_tool_results = []
                    await self._deliver_delivery(item.delivery, outbound=outbound)
            if pending_tool_results:
                await self._deliver_tool_results(pending_tool_results, outbound=outbound)
            if outbound.prompt is not None:
                await self.prompt_user(outbound.prompt)
        finally:
            outbound.mark_delivered()

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        if prompt.conversation_key and prompt.choices:
            self._pending_choices[prompt.conversation_key] = list(prompt.choices)
        source = prompt.metadata.get("source")
        if source is None:
            return ""
        reply_to = prompt.metadata.get("reply_to")
        await self._send_text(
            str(source),
            self._prompt_text(prompt),
            reply_to=str(reply_to) if reply_to is not None else None,
            reply_markup=self._choice_reply_markup(prompt.choices) if prompt.choices else None,
        )
        return ""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        inbound = self._active_inbound.get()
        if inbound is None:
            return ApprovalDecision("deny", "telegram approval has no active inbound context")
        chat_type = str(inbound.metadata.get("telegram_chat_type") or "private")
        if chat_type != "private":
            await self._send_text(
                inbound.source,
                "Telegram approval is only supported in private chat. Please retry from a private chat with this bot.",
                reply_to=inbound.reply_to,
            )
            return ApprovalDecision("deny", "telegram approval is only supported in private chat")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        approval_id = self._next_approval_id()
        conversation_key = inbound.conversation_key or f"telegram:{inbound.source}"
        pending = TelegramPendingApproval(
            request=request,
            future=future,
            source=inbound.source,
            reply_to=inbound.reply_to,
            conversation_key=conversation_key,
        )
        sent = await self._send_text(
            inbound.source,
            self._approval_text(request),
            reply_to=inbound.reply_to,
            reply_markup=self._approval_reply_markup(approval_id),
        )
        pending.message_id = _telegram_message_id(sent)
        self._pending_approvals[approval_id] = pending
        self._conversation_state(conversation_key).pending_approval_id = approval_id
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=self.approval_timeout_seconds)
        except asyncio.TimeoutError:
            decision = ApprovalDecision("deny", "telegram approval timed out")
            pending = self._resolve_pending_approval(approval_id, decision)
            if pending is not None:
                await self._edit_approval_message(pending, "Approval expired", "Request denied after 10 minutes.")
            else:
                await self._send_text(inbound.source, "Approval timed out; request denied.", reply_to=inbound.reply_to)
            return decision
        except asyncio.CancelledError:
            self._resolve_pending_approval(approval_id, ApprovalDecision("deny", "telegram approval cancelled"))
            raise

    async def _deliver_text(self, delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> None:
        text = delivery.text or delivery.fallback_text
        if not delivery.visible or not text:
            return
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        await self._send_text(str(source), text, reply_to=str(reply_to) if reply_to is not None else None)

    async def _deliver_tool_results(self, records, *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet" or not records:
            return
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        text = self._tool_results_text(records)
        if text:
            await self._send_text(str(source), text, reply_to=str(reply_to) if reply_to is not None else None)

    async def _deliver_tool_call(self, record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet":
            return
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        text = self._tool_call_text(record)
        if not text:
            return
        key = self._tool_message_key(record, outbound)
        if record.phase == "finish":
            target = self._tool_message_ids.pop(key, None)
            if target is not None and await self._edit_tool_call_message(target[0], target[1], text):
                return
        sent = await self._send_text(str(source), text, reply_to=str(reply_to) if reply_to is not None else None)
        message_id = _telegram_message_id(sent)
        if record.phase == "start" and message_id is not None:
            self._tool_message_ids[key] = (str(source), message_id)

    async def _edit_tool_call_message(self, chat_id: str, message_id: int, text: str) -> bool:
        if not hasattr(self.api, "edit_message_text"):
            return False
        try:
            if self.message_format == "plain":
                await asyncio.to_thread(
                    self.api.edit_message_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=None,
                    reply_markup=None,
                )
                return True
            await asyncio.to_thread(
                self.api.edit_message_text,
                chat_id=chat_id,
                message_id=message_id,
                text=format_telegram_markdown_v2(text),
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
            return True
        except Exception as exc:
            logger.warning("telegram tool message edit failed, sending final tool message: %s", exc)
            return False

    def _tool_message_key(self, record: ToolInteractionRecord, outbound: InteractionOutbound) -> tuple[str, str]:
        conversation = str(outbound.metadata.get("conversation_key") or outbound.metadata.get("source") or "")
        return (conversation, record.call.id)

    def _tool_call_text(self, record: ToolInteractionRecord) -> str:
        status = record.status
        if record.phase == "start":
            return f"## Tool call\n`{record.call.name}` - `running` - {self._shorten(_tool_call_start_summary(record.call), limit=220)}"
        result = ""
        if record.result is not None:
            result = self._shorten(record.result.display_output or record.result.content or "", limit=220)
        if self.tool_display == "full" and record.result is not None:
            sections = [
                "## Tool call",
                "",
                f"### `{record.call.name}` - `{status}`",
                "",
                "**Arguments**",
                "```json",
                self._shorten(json.dumps(record.call.arguments, ensure_ascii=False, indent=2), limit=1800),
                "```",
                "",
                "**Result**",
                "```",
                self._shorten(record.result.display_output or record.result.content or "", limit=1800),
                "```",
            ]
            if record.result.model_output and record.result.model_output != record.result.content:
                sections.extend(["", "**Model output**", "```", self._shorten(record.result.model_output, limit=1200), "```"])
            return "\n".join(sections)
        return f"## Tool call\n`{record.call.name}` - `{status}` - {result}"

    def _tool_results_text(self, records) -> str:
        if self.tool_display == "full":
            sections: list[str] = ["## Tool calls"]
            for index, record in enumerate(records, start=1):
                status = "error" if record.result.is_error else "ok"
                sections.extend(
                    [
                        "",
                        f"### {index}. `{record.call.name}` - `{status}`",
                        "",
                        "**Arguments**",
                        "```json",
                        self._shorten(json.dumps(record.call.arguments, ensure_ascii=False, indent=2), limit=1800),
                        "```",
                        "",
                        "**Result**",
                        "```",
                        self._shorten(record.result.display_output or record.result.content or "", limit=1800),
                        "```",
                    ]
                )
                if record.result.model_output and record.result.model_output != record.result.content:
                    sections.extend(
                        [
                            "",
                            "**Model output**",
                            "```",
                            self._shorten(record.result.model_output, limit=1200),
                            "```",
                        ]
                    )
            return "\n".join(sections)

        lines = ["## Tool calls"]
        for index, record in enumerate(records, start=1):
            status = "error" if record.result.is_error else "ok"
            result = self._shorten(record.result.display_output or record.result.content or "", limit=220)
            lines.append(f"{index}. `{record.call.name}` - `{status}` - {result}")
        return "\n".join(lines)

    async def _deliver_delivery(self, delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> None:
        if not delivery.visible:
            return
        blocks = delivery.blocks or []
        if not blocks:
            await self._deliver_text(delivery, outbound=outbound)
            return
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        reply_to_text = str(reply_to) if reply_to is not None else None
        fallback_lines: list[str] = []
        for index, block in enumerate(blocks):
            block_type = str(block.get("type") or "text")
            if block_type == "text":
                text = str(block.get("text") or "")
                if text:
                    await self._send_text(
                        str(source),
                        text,
                        reply_to=reply_to_text if index == 0 else None,
                    )
                continue
            delivered = await self._deliver_media_block(
                str(source),
                block,
                reply_to=reply_to_text if index == 0 else None,
            )
            if not delivered:
                fallback = self._media_block_fallback(block)
                if fallback:
                    fallback_lines.append(fallback)
        if fallback_lines:
            await self._send_text(str(source), "\n\n".join(fallback_lines), reply_to=reply_to_text)

    async def _deliver_media_block(self, chat_id: str, block: dict[str, Any], *, reply_to: str | None = None) -> bool:
        artifact = block.get("artifact")
        if not isinstance(artifact, dict):
            return False
        source = self._artifact_send_source(artifact)
        if not source:
            return False
        reply_to_id = int(reply_to) if isinstance(reply_to, str) and reply_to.isdigit() else None
        caption = str(block.get("text") or artifact.get("summary") or "") or None
        block_type = str(block.get("type") or artifact.get("kind") or "file")
        try:
            if block_type == "image":
                await asyncio.to_thread(
                    self.api.send_photo,
                    chat_id=chat_id,
                    photo=source,
                    caption=caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            elif block_type == "audio":
                await self._deliver_voice_block(
                    chat_id=chat_id,
                    source=source,
                    caption=caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            elif block_type == "video":
                await asyncio.to_thread(
                    self.api.send_video,
                    chat_id=chat_id,
                    video=source,
                    caption=caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            else:
                await asyncio.to_thread(
                    self.api.send_document,
                    chat_id=chat_id,
                    document=source,
                    caption=caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            return True
        except Exception as exc:
            logger.warning("telegram media delivery failed, falling back to text: %s", exc)
            return False

    async def _deliver_voice_block(
        self,
        *,
        chat_id: str,
        source: str,
        caption: str | None,
        reply_to_message_id: int | None,
    ) -> None:
        path = Path(source)
        if path.exists() and path.is_file():
            with tempfile.TemporaryDirectory(prefix="demiurge-telegram-voice-") as tmpdir:
                voice_path = Path(tmpdir) / f"{path.stem or 'voice'}.ogg"
                await asyncio.to_thread(_convert_audio_to_ogg_opus, path, voice_path)
                await asyncio.to_thread(
                    self.api.send_voice,
                    chat_id=chat_id,
                    voice=str(voice_path),
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                )
            return
        await asyncio.to_thread(
            self.api.send_voice,
            chat_id=chat_id,
            voice=source,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    def _artifact_send_source(self, artifact: dict[str, Any]) -> str | None:
        url = artifact.get("url")
        if url:
            return str(url)
        path = artifact.get("resolved_path") or artifact.get("path")
        if not path:
            return None
        return str(path)

    def _media_block_fallback(self, block: dict[str, Any]) -> str:
        artifact = block.get("artifact")
        if not isinstance(artifact, dict):
            return ""
        summary = artifact.get("summary") or artifact.get("media_type") or artifact.get("kind") or block.get("type")
        artifact_id = artifact.get("artifact_id") or "artifact"
        caption = block.get("text")
        prefix = f"{caption}\n" if caption else ""
        return f"{prefix}[artifact:{artifact_id} {artifact.get('kind') or block.get('type')} {summary}]"

    def _conversation_state(self, conversation_key: str) -> TelegramConversationState:
        state = self._conversations.get(conversation_key)
        if state is None:
            state = TelegramConversationState(
                runtime=self._runtime_factory(conversation_key),
                busy_mode=self.default_busy_mode,
                route_binding=SessionRouteBinding(route=self),
                conversation_key=conversation_key,
            )
            self._conversations[conversation_key] = state
            self._subscribe_task_worker(state.runtime)
        return state

    async def _handle_busy_inbound(self, state: TelegramConversationState, inbound: InteractionInbound) -> None:
        async def notify(decision) -> None:
            if decision.kind == "queue":
                await self._send_text(inbound.source, f"Queued for next turn: {self._shorten(inbound.text)}", reply_to=inbound.reply_to)
                return
            if decision.kind == "interrupt":
                await self._send_text(
                    inbound.source,
                    f"Interrupting current turn; queued latest input: {self._shorten(inbound.text)}",
                    reply_to=inbound.reply_to,
                )

        await ConversationTurnController(state).handle_busy_inbound(inbound, notify=notify)

    def _start_turn(self, state: TelegramConversationState, inbound: InteractionInbound) -> None:
        ConversationTurnController(state).start(inbound, lambda next_inbound: self._run_inbound(state, next_inbound))

    async def _run_inbound(self, state: TelegramConversationState, inbound: InteractionInbound) -> None:
        task = asyncio.current_task()
        token = self._active_inbound.set(inbound)
        try:
            if self.send_typing:
                await self._send_typing(inbound.source)
            outbound = await state.runtime.handle(inbound, route_binding=state.route_binding)
            await self.deliver(outbound)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("telegram turn failed")
            await self._send_text(inbound.source, f"Turn failed: {exc}", reply_to=inbound.reply_to)
        finally:
            self._active_inbound.reset(token)
            controller = ConversationTurnController(state)
            controller.finish(task)
            await controller.drain_next(lambda next_inbound: self._run_inbound(state, next_inbound))

    async def _drain_next_queued_input(self, state: TelegramConversationState) -> None:
        await ConversationTurnController(state).drain_next(
            lambda next_inbound: self._run_inbound(state, next_inbound)
        )

    async def _handle_telegram_command(
        self,
        inbound: InteractionInbound,
        state: TelegramConversationState,
    ):
        async def send_notice(text: str) -> None:
            await self._send_text(inbound.source, text, reply_to=inbound.reply_to)

        return await self._command_runtime.handle(
            inbound,
            state,
            handlers=self._telegram_command_handlers(),
            send_notice=send_notice,
        )

    def _telegram_command_handlers(self):
        return {
            "help": self._command_help,
            "status": self._command_status,
            "new": self._command_new,
            "stop": self._command_stop,
            "queue": self._command_queue,
            "busy": self._command_busy,
            "sessions": self._command_sessions,
            "subagents": self._command_subagents,
            "resume": self._command_resume,
            "tools": self._command_tools,
            "skills": self._command_skills,
            "skill": self._command_skill,
        }

    async def _command_help(self, _: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        await self._send_text(inbound.source, help_text_for_surface("telegram"), reply_to=inbound.reply_to)

    async def _command_status(self, _: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        session_runtime = getattr(state.runtime, "session_runtime", None) or getattr(
            state.runtime.runner,
            "session_runtime",
            None,
        )
        view = build_runtime_status_view(
            state.runtime.runner,
            session_runtime,
            running=ConversationTurnController(state).running,
            busy_mode=state.busy_mode,
            queued_inputs=state.queue.qsize(),
        )
        await self._send_text(
            inbound.source,
            format_runtime_status_markdown(
                view,
                extra_lines=(
                    "- access: `restricted`",
                    f"- allowed users: `{len(self.allowed_users)}`",
                    f"- allowed chats: `{len(self.allowed_chats)}`",
                    f"- current authorized: `{str(self._inbound_authorized(inbound)).lower()}`",
                ),
            ),
            reply_to=inbound.reply_to,
        )

    async def _command_new(self, _: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        await self._cancel_active(state)
        self._clear_queue(state, preserve_completions=False)
        runner = state.runtime.runner
        if not hasattr(runner, "start_new_session"):
            await self._send_text(inbound.source, "Session reset is not available.", reply_to=inbound.reply_to)
            return
        await runner.prepare_live_core()
        session_id = runner.start_new_session(
            channel="telegram",
            conversation_key=inbound.conversation_key,
            source=inbound.source,
            reply_to=inbound.reply_to,
            replace_conversation_binding=True,
        )
        state.route_binding.bind(runner.interaction_router, session_id)
        await self._send_text(inbound.source, f"New session: `{session_id}`", reply_to=inbound.reply_to)

    async def _command_stop(self, _: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        running = ConversationTurnController(state).running
        queued = self._clear_queue(state, preserve_completions=True)
        if running:
            await self._cancel_active(state)
            await self._send_text(inbound.source, f"Stopped current turn; cleared {queued} queued message(s).", reply_to=inbound.reply_to)
            return
        await self._send_text(inbound.source, f"No running turn; cleared {queued} queued message(s).", reply_to=inbound.reply_to)
        await self._drain_next_queued_input(state)

    async def _command_queue(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        text = args.strip()
        if not text:
            await self._send_text(inbound.source, "Usage: `/queue <prompt>`", reply_to=inbound.reply_to)
            return
        queued = InteractionInbound(
            channel=inbound.channel,
            text=text,
            source=inbound.source,
            reply_to=inbound.reply_to,
            conversation_key=inbound.conversation_key,
            metadata=dict(inbound.metadata),
        )
        await ConversationTurnController(state).queue_and_drain_if_idle(
            queued,
            lambda next_inbound: self._run_inbound(state, next_inbound),
        )
        await self._send_text(inbound.source, f"Queued: {self._shorten(text)}", reply_to=inbound.reply_to)

    async def _command_busy(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        mode = args.strip().lower()
        if not mode:
            await self._send_text(inbound.source, f"Busy mode: `{state.busy_mode}`", reply_to=inbound.reply_to)
            return
        if mode not in {"interrupt", "queue"}:
            await self._send_text(inbound.source, "Usage: `/busy interrupt|queue`", reply_to=inbound.reply_to)
            return
        state.busy_mode = mode
        await self._send_text(inbound.source, f"Busy mode: `{state.busy_mode}`", reply_to=inbound.reply_to)

    async def _command_sessions(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        limit = int(args.strip()) if args.strip().isdigit() else 10
        view = build_session_list_view(
            state.runtime.session_runtime,
            core_id=state.runtime.runner.core_id,
            active_session_id=state.runtime.runner.session_id,
            limit=limit,
        )
        await self._send_text(inbound.source, view.text(), reply_to=inbound.reply_to)

    async def _command_subagents(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        text = await subagents_command_text(
            state.runtime.runner.task_worker,
            session_id=state.runtime.runner.session_id,
            args=args,
        )
        await self._send_text(inbound.source, text[:3800], reply_to=inbound.reply_to)

    async def _command_resume(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        raw = args.strip()
        view = build_session_list_view(
            state.runtime.session_runtime,
            core_id=state.runtime.runner.core_id,
            active_session_id=state.runtime.runner.session_id,
            limit=20,
        )
        if not raw:
            await self._send_text(
                inbound.source,
                view.text() + "\n\nUse `/resume <number|session_id>`.",
                reply_to=inbound.reply_to,
            )
            return
        resolution = resolve_session_choice(raw, view)
        if not resolution.ok:
            await self._send_text(
                inbound.source,
                resolution.message or "Invalid session selection.",
                reply_to=inbound.reply_to,
            )
            return
        assert resolution.session_id is not None
        try:
            state.runtime.runner.resume_session(resolution.session_id)
        except FileNotFoundError as exc:
            await self._send_text(inbound.source, str(exc), reply_to=inbound.reply_to)
            return
        state.route_binding.bind(state.runtime.runner.interaction_router, resolution.session_id)
        await self._send_text(inbound.source, f"Resumed session: `{resolution.session_id}`", reply_to=inbound.reply_to)

    async def _command_tools(self, _: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        runner = state.runtime.runner
        core = await runner.load_active_core()
        lines = ["# Tools"]
        for entry in runner.tool_runtime.registry_for(core):
            lines.append(f"- `{entry.name}` - {entry.source} - {entry.approval_policy}")
        await self._send_text(inbound.source, "\n".join(lines), reply_to=inbound.reply_to)

    async def _command_skills(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        runner = state.runtime.runner
        core = await runner.load_active_core()
        category = args.strip() or None
        skills = [skill for skill in core.skills if category is None or skill.category == category]
        lines = ["# Skills"]
        for skill in skills:
            lines.append(f"- `{skill.name}` - {skill.category} - {skill.description}")
        await self._send_text(inbound.source, "\n".join(lines), reply_to=inbound.reply_to)

    async def _command_skill(self, args: str, inbound: InteractionInbound, state: TelegramConversationState) -> None:
        parts = args.split(maxsplit=1)
        if not parts:
            await self._send_text(inbound.source, "Usage: `/skill <name>`", reply_to=inbound.reply_to)
            return
        runner = state.runtime.runner
        core = await runner.load_active_core()
        result = await runner.tool_runtime.execute(
            ToolCall(name="skill_view", arguments={"name": parts[0]}, id="telegram_skill_view"),
            core=core,
            turn=TurnContext(
                session_id=runner.session_id,
                turn_id="telegram_slash",
                core_id=core.core_id,
                core_revision=runner.version_store.active_pointer(core.core_id).active_revision,
                user_input=AgentInput(content=inbound.text, metadata=dict(inbound.metadata)),
                metadata=dict(inbound.metadata),
            ),
            capability=CapabilityFacade(core),
            emit_event=runner.event_log.emit,
        )
        content = result.content
        if isinstance(result.data, dict) and result.data.get("content"):
            content = str(result.data["content"])
        await self._send_text(inbound.source, content[:3800], reply_to=inbound.reply_to)

    async def _cancel_active(self, state: TelegramConversationState) -> None:
        await ConversationTurnController(state).cancel_active(
            before_cancel=lambda: self._cancel_pending_approval(state, reason="telegram turn stopped")
        )

    def _clear_queue(self, state: TelegramConversationState, *, preserve_completions: bool) -> int:
        return ConversationTurnController(state).clear_queue(preserve_completions=preserve_completions)

    def _remember_route(self, state: TelegramConversationState, inbound: InteractionInbound) -> None:
        state.remember_route(inbound)

    def _merge_stored_task_completions(
        self,
        state: TelegramConversationState,
        inbound: InteractionInbound,
    ) -> InteractionInbound:
        completions = state.claim_pending_completions(
            channel="telegram",
            owner_id="bridge:telegram:merge",
            fallback_source=inbound.source,
        )
        if not completions:
            return inbound
        return state.queue.merge_completions_into(inbound, stored_completions=completions)

    def _subscribe_task_worker(self, runtime: InteractionRuntime) -> None:
        task_worker = getattr(getattr(runtime, "runner", None), "task_worker", None)
        if task_worker is None or task_worker is self._task_worker:
            return
        if self._task_unsubscribe is not None:
            self._task_unsubscribe()
        self._task_worker = task_worker
        self._task_unsubscribe = task_worker.subscribe(self._on_task_completion)

    def _on_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        state = self._state_for_session(event.owner_session_id)
        if state is None:
            return
        try:
            asyncio.get_running_loop().create_task(self._enqueue_task_completion(state, event))
        except RuntimeError:
            return

    async def _enqueue_task_completion(self, state: TelegramConversationState, event: RuntimeTaskCompletionEvent) -> None:
        if not state.source:
            return
        if self._task_worker is None:
            return
        inbound = state.claim_completion_event(
            event,
            channel="telegram",
            owner_id="bridge:telegram:enqueue",
            task_worker=self._task_worker,
        )
        if inbound is None:
            return
        await ConversationTurnController(state).enqueue_completion(
            inbound,
            lambda next_inbound: self._run_inbound(state, next_inbound),
        )

    def _state_for_session(self, session_id: str) -> TelegramConversationState | None:
        for state in self._conversations.values():
            if state.session_id == session_id:
                return state
        return None

    async def _send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        chunk_index: int = 0,
    ) -> dict[str, Any] | None:
        if not text.strip():
            return None
        first_result: dict[str, Any] | None = None
        reply_to_id = int(reply_to) if isinstance(reply_to, str) and reply_to.isdigit() else None
        if self.message_format == "plain":
            for index, chunk in enumerate(split_telegram_message(text, markdown_v2=False)):
                result = await asyncio.to_thread(
                    self.api.send_message,
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, index, self.reply_to_mode) else None,
                    parse_mode=None,
                    reply_markup=reply_markup if index == 0 else None,
                )
                first_result = first_result or result
            return first_result

        if reply_markup is None and self._should_send_rich(text):
            try:
                return await asyncio.to_thread(
                    self.api.send_rich_message,
                    chat_id=chat_id,
                    markdown=text,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            except Exception as exc:
                if _is_rich_capability_error(exc):
                    self._rich_messages_disabled = True
                if _is_rich_fallback_error(exc):
                    logger.warning("telegram rich message failed, falling back to MarkdownV2: %s", exc)
                else:
                    logger.warning("telegram rich message transient failure, not retrying delivery: %s", exc)
                    return None

        formatted = format_telegram_markdown_v2(text)
        chunks = split_telegram_message(formatted, markdown_v2=True)
        for index, chunk in enumerate(chunks):
            markup = reply_markup if index == 0 else None
            try:
                result = await asyncio.to_thread(
                    self.api.send_message,
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, index, self.reply_to_mode) else None,
                    parse_mode="MarkdownV2",
                    reply_markup=markup,
                )
                first_result = first_result or result
            except Exception as exc:
                if not _is_markdown_error(exc):
                    raise
                logger.warning("telegram MarkdownV2 send failed, falling back to plain text: %s", exc)
                for plain_index, plain_chunk in enumerate(split_telegram_message(_strip_mdv2(chunk), markdown_v2=False)):
                    result = await asyncio.to_thread(
                        self.api.send_message,
                        chat_id=chat_id,
                        text=plain_chunk,
                        reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, index, self.reply_to_mode) else None,
                        parse_mode=None,
                        reply_markup=markup if plain_index == 0 else None,
                    )
                    first_result = first_result or result
        return first_result

    def _should_send_rich(self, text: str) -> bool:
        return bool(
            self.rich_messages
            and not self._rich_messages_disabled
            and self.message_format == "markdown_v2"
            and hasattr(self.api, "send_rich_message")
            and _needs_rich_telegram_rendering(text)
        )

    async def _send_typing(self, chat_id: str) -> None:
        if not hasattr(self.api, "send_chat_action"):
            return
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.api.send_chat_action, chat_id=chat_id, action="typing")

    async def _answer_callback_query(self, callback_query_id: str, *, text: str | None = None) -> None:
        if not hasattr(self.api, "answer_callback_query"):
            return
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.api.answer_callback_query, callback_query_id=callback_query_id, text=text)

    def _consume_pending_choice(self, conversation_key: str, text: str) -> str:
        choices = self._pending_choices.get(conversation_key)
        if not choices:
            return text
        value = text.strip()
        self._pending_choices.pop(conversation_key, None)
        if value.isdigit():
            index = int(value) - 1
            if 0 <= index < len(choices):
                return choices[index]
        return text

    def _prompt_text(self, prompt: UserPromptRequest) -> str:
        lines = [prompt.question]
        for index, choice in enumerate(prompt.choices, start=1):
            lines.append(f"{index}. {choice}")
        return "\n".join(lines)

    def _choice_reply_markup(self, choices: list[str]) -> dict[str, Any]:
        buttons = []
        for index, choice in enumerate(choices):
            label = f"{index + 1}. {self._shorten(choice, limit=32)}"
            buttons.append([{"text": label, "callback_data": f"choice:{index}"}])
        return {"inline_keyboard": buttons}

    def _next_approval_id(self) -> str:
        self._approval_counter += 1
        return str(self._approval_counter)

    def _approval_reply_markup(self, approval_id: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "Allow once", "callback_data": f"approval:{approval_id}:allow"}],
                [{"text": "Allow for session", "callback_data": f"approval:{approval_id}:session"}],
                [{"text": "Deny", "callback_data": f"approval:{approval_id}:deny"}],
            ]
        }

    def _approval_text(self, request: ApprovalRequest) -> str:
        lines = [
            "## Approval required",
            "",
            f"**Summary:** {request.summary}",
            f"**Tool:** `{request.tool_name}`",
            f"**Risk:** `{request.risk}`",
            f"**Capability:** `{request.capability}`",
            f"**Action:** `{request.action}`",
        ]
        if request.target:
            lines.append(f"**Target:** `{request.target}`")
        if request.command:
            lines.extend(["", "**Command**", "```", self._shorten(request.command, limit=1000), "```"])
        if request.arguments_preview:
            preview = json.dumps(request.arguments_preview, ensure_ascii=False, sort_keys=True, indent=2)
            lines.extend(["", "**Arguments**", "```json", self._shorten(preview, limit=1000), "```"])
        lines.extend(["", "This request expires in 10 minutes.", "Choose **Allow once**, **Allow for session**, or **Deny**."])
        return "\n".join(lines)

    def _resolved_approval_text(self, request: ApprovalRequest, *, title: str, detail: str) -> str:
        lines = [
            f"## {title}",
            "",
            detail,
            "",
            f"**Summary:** {request.summary}",
            f"**Tool:** `{request.tool_name}`",
        ]
        if request.command:
            lines.extend(["", "**Command**", "```", self._shorten(request.command, limit=1000), "```"])
        return "\n".join(lines)

    async def _handle_approval_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        data = str(callback.get("data") or "")
        parts = data.split(":")
        if len(parts) != 3:
            if callback_id:
                await self._answer_callback_query(str(callback_id), text="Invalid approval action.")
            return
        _, approval_id, action = parts
        decisions = {
            "allow": ApprovalDecision("allow", "approved by Telegram user"),
            "session": ApprovalDecision("always_allow_for_session", "approved by Telegram user for this session"),
            "deny": ApprovalDecision("deny", "denied by Telegram user"),
        }
        labels = {
            "allow": ("Approved once", "The command was approved for this request."),
            "session": ("Approved for session", "Matching requests are allowed for this session."),
            "deny": ("Denied", "The command was not executed."),
        }
        decision = decisions.get(action)
        if decision is None:
            if callback_id:
                await self._answer_callback_query(str(callback_id), text="Invalid approval action.")
            return
        if approval_id not in self._pending_approvals:
            if callback_id:
                await self._answer_callback_query(str(callback_id), text="Approval expired.")
            await self._edit_expired_callback_message(callback)
            return
        pending = self._resolve_pending_approval(approval_id, decision)
        if callback_id:
            await self._answer_callback_query(str(callback_id), text="Approved." if decision.allowed else "Denied.")
        if pending is not None:
            title, detail = labels[action]
            await self._edit_approval_message(pending, title, detail)

    def _resolve_pending_approval(self, approval_id: str, decision: ApprovalDecision) -> TelegramPendingApproval | None:
        pending = self._pending_approvals.pop(approval_id, None)
        if pending is None:
            return None
        state = self._conversations.get(pending.conversation_key)
        if state is not None and state.pending_approval_id == approval_id:
            state.pending_approval_id = None
        if not pending.future.done():
            pending.future.set_result(decision)
        return pending

    async def _cancel_pending_approval(self, state: TelegramConversationState, *, reason: str) -> None:
        approval_id = state.pending_approval_id
        if approval_id is None:
            return
        pending = self._resolve_pending_approval(approval_id, ApprovalDecision("deny", reason))
        if pending is not None:
            await self._edit_approval_message(pending, "Approval expired", "The turn was stopped before approval.")

    async def _edit_approval_message(self, pending: TelegramPendingApproval, title: str, detail: str) -> None:
        if pending.message_id is None or not hasattr(self.api, "edit_message_text"):
            return
        text = self._resolved_approval_text(pending.request, title=title, detail=detail)
        formatted = format_telegram_markdown_v2(text)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                self.api.edit_message_text,
                chat_id=pending.source,
                message_id=pending.message_id,
                text=formatted,
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
        if hasattr(self.api, "edit_message_reply_markup"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.api.edit_message_reply_markup,
                    chat_id=pending.source,
                    message_id=pending.message_id,
                    reply_markup=None,
                )

    async def _edit_expired_callback_message(self, callback: dict[str, Any]) -> None:
        if not hasattr(self.api, "edit_message_text"):
            return
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if chat_id is None or not isinstance(message_id, int):
            return
        formatted = format_telegram_markdown_v2("## Approval expired\n\nThis approval has already been resolved.")
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                self.api.edit_message_text,
                chat_id=str(chat_id),
                message_id=message_id,
                text=formatted,
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
        if hasattr(self.api, "edit_message_reply_markup"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.api.edit_message_reply_markup,
                    chat_id=str(chat_id),
                    message_id=message_id,
                    reply_markup=None,
                )

    def _normalize_text_for_chat(self, text: str, *, chat_type: str, message: dict[str, Any]) -> str | None:
        if chat_type == "private":
            return text
        stripped = text.strip()
        command = parse_slash_command(stripped)
        if command and stripped.startswith("/"):
            command_token, _, rest = stripped.partition(" ")
            if command.name == "ask":
                if "@" in command_token:
                    _, _, command_user = command_token.partition("@")
                    if self.bot_username and command_user.lower() != self.bot_username.lower():
                        return None
                return rest
            if self._telegram_command_mentioned_bot(command_token):
                return f"/{command.name}" + (f" {rest}" if rest else "")
            if not self.bot_username and command.name in command_names_for_surface("telegram"):
                return stripped
            return None
        if self.bot_username and f"@{self.bot_username}".lower() in stripped.lower():
            return re.sub(f"@{re.escape(self.bot_username)}", "", stripped, flags=re.IGNORECASE).strip()
        reply = message.get("reply_to_message") or {}
        reply_from = reply.get("from") or {}
        if reply_from.get("is_bot") and (
            not self.bot_username or reply_from.get("username", "").lower() == self.bot_username.lower()
        ):
            return stripped
        return None

    def _telegram_command_mentioned_bot(self, command_token: str) -> bool:
        if "@" not in command_token:
            return False
        _, _, command_user = command_token.partition("@")
        return bool(self.bot_username and command_user.lower() == self.bot_username.lower())

    def _shorten(self, text: str, *, limit: int = 80) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 3)] + "..."


def _should_thread_reply(reply_to: int | None, chunk_index: int, mode: str) -> bool:
    if reply_to is None:
        return False
    if mode == "off":
        return False
    if mode == "all":
        return True
    return chunk_index == 0


def _convert_audio_to_ogg_opus(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "libopus",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-b:a",
        "32k",
        str(target),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to send Telegram voice messages") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg audio conversion failed: {detail or completed.returncode}")
    if not target.exists():
        raise RuntimeError("ffmpeg audio conversion did not produce an output file")


def build_telegram_gateway_bridge(app: Any, config: TelegramChannelConfig) -> TelegramInteractionBridge:
    return TelegramInteractionBridge.from_config(
        None,
        config,
        runtime_factory=_runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
    )


def _runtime_factory_for_app(app: Any) -> Callable[[str], InteractionRuntime]:
    if not all(hasattr(app, name) for name in ("home", "version_store", "core_loader", "tool_runtime")):
        runtime = InteractionRuntime(app.runner)
        return lambda _conversation_key: runtime

    def make_runtime(_conversation_key: str) -> InteractionRuntime:
        runner = SessionTurnStepRunner(
            home=app.home,
            version_store=app.version_store,
            core_loader=app.core_loader,
            provider=app.runner.provider,
            tool_runtime=app.tool_runtime,
            core_id=app.runner.core_id,
            model_override=app.runner.model_override,
            model_resolver=app.runner.model_resolver,
            provider_name=app.runner.provider_name,
            workspace=app.runner.workspace,
            show_system_prompt=app.runner.show_system_prompt,
            runtime_timezone=app.runtime_timezone,
            task_worker=app.task_worker,
            session_runtime=app.session_runtime,
            interaction_router=app.runner.interaction_router,
            prepare_live_core=app.prepare_live_core,
        )
        return InteractionRuntime(runner)

    return make_runtime


def _resolve_telegram_token(config: TelegramChannelConfig) -> str | None:
    if config.bot_token_env:
        value = os.environ.get(config.bot_token_env)
        if value:
            return value
    return config.bot_token


def _is_markdown_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "parse" in message or "markdown" in message or "entity" in message


def _is_transient_telegram_transport_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            ssl.SSLError,
            TimeoutError,
            ConnectionError,
            socket.timeout,
            http.client.RemoteDisconnected,
        ),
    )


def _is_rich_capability_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "code", None) or getattr(exc, "error_code", None)
    if status == 404:
        return True
    if isinstance(exc, (AttributeError, TypeError, NotImplementedError)):
        return True
    message = str(exc).lower()
    endpoint_missing = "endpoint" in message and ("not found" in message or "does not exist" in message)
    method_missing = "method" in message and ("not found" in message or "does not exist" in message)
    no_such_method = "no such method" in message
    unsupported_endpoint = "unsupported" in message or "not implemented" in message
    return no_such_method or endpoint_missing or method_missing or unsupported_endpoint


def _is_rich_fallback_error(exc: Exception) -> bool:
    if _is_rich_capability_error(exc):
        return True
    status = getattr(exc, "status", None) or getattr(exc, "code", None) or getattr(exc, "error_code", None)
    if status == 400:
        return True
    message = str(exc).lower()
    parse_rich_error = "parse" in message and "rich" in message
    return (
        "bad request" in message
        or "can't parse" in message
        or parse_rich_error
    )
