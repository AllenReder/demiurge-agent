from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import http.client
import logging
import mimetypes
import os
import re
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from demiurge.channels.commands import ChannelCommandExecutor, ChannelCommandRuntime
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.security.redaction import (
    RedactionView,
    SecretValue,
    redact_exception,
    redact_exception_message,
)
from demiurge.core import TelegramChannelConfig
from demiurge.runtime.conversation_keys import build_conversation_key
from demiurge.runtime.conversation_lifecycle import ConversationLifecycleConfig, ConversationLifecycleRuntime
from demiurge.runtime.interaction_factory import runtime_factory_for_app
from demiurge.runtime.tool_display import normalize_tool_display, tool_call_markdown, tool_results_markdown
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionOutbound,
    InteractionRuntime,
    SessionRouteBinding,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.ingress import BusyInboundDecision, ConversationIngressState
from demiurge.runtime.approvals import (
    ApprovalPromptRuntime,
    PendingApproval,
    approval_button_rows,
    approval_callback_answer,
    approval_decision_for_action,
    approval_resolution,
    format_approval_request_text,
    format_resolved_approval_text,
    parse_approval_callback_data,
)
from demiurge.runtime.outbound_delivery import (
    NativeDeliveryRuntime,
    NativeMediaRequest,
    TextOutboundTarget,
    TextOutboundDeliveryRuntime,
    text_outbound_target,
)
from demiurge.runtime.prompts import PromptDeliveryRuntime, choice_button_rows
from demiurge.slash import command_names_for_surface, parse_slash_command, telegram_command_specs
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
    source: str
    reply_to: str | None
    conversation_key: str
    message_id: int | None = None
    message_ready: asyncio.Event = field(
        default_factory=asyncio.Event,
        repr=False,
    )


class TelegramInboundMediaError(RuntimeError):
    pass


TELEGRAM_APPROVAL_TIMEOUT_SECONDS = 600
TELEGRAM_POLL_NETWORK_BASE_DELAY_SECONDS = 1.0
TELEGRAM_POLL_NETWORK_MAX_DELAY_SECONDS = 30.0
TELEGRAM_POLL_CONFLICT_BASE_DELAY_SECONDS = 15.0
TELEGRAM_POLL_CONFLICT_STEP_DELAY_SECONDS = 10.0
TELEGRAM_POLL_CONFLICT_MAX_RETRIES = 5
TELEGRAM_ATTACHMENT_PLACEHOLDER = "[telegram attachment]"
TELEGRAM_INBOUND_MEDIA_MAX_BYTES = 20 * 1024 * 1024
TELEGRAM_INBOUND_MEDIA_CACHE_DIR = ".demiurge-telegram"
_TELEGRAM_DEFAULT_MEDIA_TYPES = {
    "image": "image/jpeg",
    "audio": "audio/ogg",
    "video": "video/mp4",
    "document": "application/octet-stream",
}
_TELEGRAM_DEFAULT_EXTENSIONS = {
    "image": ".jpg",
    "audio": ".ogg",
    "video": ".mp4",
    "document": ".bin",
}
_TELEGRAM_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


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
        workspace_root: str | Path | None = None,
        media_cache_root: str | Path | None = None,
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
        self.tool_display = normalize_tool_display(tool_display)
        self.media_cache_root = _resolve_media_cache_root(
            workspace_root=workspace_root,
            media_cache_root=media_cache_root,
        )
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
        self._prompt_delivery = PromptDeliveryRuntime()
        self._pending_approvals = ApprovalPromptRuntime()
        self._active_inbound: contextvars.ContextVar[InteractionInbound | None] = contextvars.ContextVar(
            "demiurge_telegram_active_inbound",
            default=None,
        )
        self._conversation_lifecycle = ConversationLifecycleRuntime(
            config=ConversationLifecycleConfig(
                channel="telegram",
                merge_owner_id="bridge:telegram:merge",
                enqueue_owner_id="bridge:telegram:enqueue",
                require_source=True,
            ),
            state_factory=self._new_conversation_state,
            run_turn=self._run_inbound,
            notify_busy=self._notify_busy_inbound,
        )
        self._command_executor = ChannelCommandExecutor(
            channel_name="telegram",
            surface="telegram",
            send_text=self._send_command_text,
            lifecycle=self._conversation_lifecycle,
            cancel_active=self._cancel_active,
            include_status_channel=False,
            status_extra_lines=self._status_extra_lines,
            include_subagents=True,
        )
        self._conversations = self._conversation_lifecycle.states
        self._tool_message_ids: dict[tuple[str, str], tuple[str, int]] = {}

    @classmethod
    def from_config(
        cls,
        runtime: InteractionRuntime | None,
        config: TelegramChannelConfig,
        *,
        runtime_factory: Callable[[str], InteractionRuntime] | None = None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
        workspace_root: str | Path | None = None,
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
            workspace_root=workspace_root,
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
        inbound = self._prompt_delivery.resolve_inbound(inbound)
        inbound = await self._resolve_inbound_media(inbound)
        if inbound is None:
            return
        await self.handle_inbound(inbound)

    def normalize_update(self, update: dict[str, Any]) -> InteractionInbound | None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            return self._normalize_callback_query(update, callback)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None
        chat_type = chat.get("type") or "private"
        user_id = (message.get("from") or {}).get("id")
        message_id = message.get("message_id")
        pending_media = self._telegram_media_descriptors(message) if chat_type == "private" else []
        raw_text = self._telegram_message_text(message, include_caption=chat_type == "private")
        media_only = raw_text is None and bool(pending_media)
        if raw_text is None and not pending_media:
            return None
        normalized = self._normalize_text_for_chat(
            raw_text or TELEGRAM_ATTACHMENT_PLACEHOLDER,
            chat_type=chat_type,
            message=message,
        )
        if normalized is None or not normalized.strip():
            return None
        conversation_key = build_conversation_key("telegram", "dm" if chat_type == "private" else "group", chat_id)
        metadata: dict[str, Any] = {
            "telegram_chat_id": chat_id,
            "telegram_chat_type": chat_type,
            "telegram_user_id": user_id,
            "telegram_update_id": update.get("update_id"),
            "telegram_message_id": message_id,
        }
        if pending_media:
            metadata["_telegram_pending_media"] = pending_media
            metadata["telegram_attachment_count"] = len(pending_media)
            metadata["telegram_media_only"] = media_only
        return InteractionInbound(
            channel="telegram",
            text=normalized.strip(),
            source=str(chat_id),
            reply_to=str(message_id) if message_id is not None else None,
            conversation_key=conversation_key,
            metadata=metadata,
        )

    def _telegram_message_text(self, message: dict[str, Any], *, include_caption: bool) -> str | None:
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            return text
        if not include_caption:
            return None
        caption = message.get("caption")
        if isinstance(caption, str) and caption.strip():
            return caption
        return None

    def _telegram_media_descriptors(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidates = [item for item in photo if isinstance(item, dict)]
            if candidates:
                selected = max(candidates, key=_telegram_photo_score)
                descriptor = self._telegram_file_descriptor(
                    selected,
                    telegram_kind="photo",
                    kind="image",
                    default_media_type="image/jpeg",
                )
                return [descriptor] if descriptor is not None else []
        for telegram_kind, kind, default_media_type in (
            ("voice", "audio", "audio/ogg"),
            ("audio", "audio", "audio/mpeg"),
            ("video", "video", "video/mp4"),
            ("document", "document", None),
        ):
            value = message.get(telegram_kind)
            if isinstance(value, dict):
                descriptor = self._telegram_file_descriptor(
                    value,
                    telegram_kind=telegram_kind,
                    kind=kind,
                    default_media_type=default_media_type,
                )
                return [descriptor] if descriptor is not None else []
        return []

    def _telegram_file_descriptor(
        self,
        value: dict[str, Any],
        *,
        telegram_kind: str,
        kind: str,
        default_media_type: str | None,
    ) -> dict[str, Any] | None:
        file_id = value.get("file_id")
        if not isinstance(file_id, str) or not file_id.strip():
            return None
        filename = _optional_text(value.get("file_name"))
        media_type = _optional_text(value.get("mime_type")) or default_media_type
        if telegram_kind == "document":
            kind = _kind_from_media_type(media_type, filename=filename, default=kind)
        descriptor: dict[str, Any] = {
            "telegram_kind": telegram_kind,
            "kind": kind,
            "file_id": file_id,
            "file_unique_id": _optional_text(value.get("file_unique_id")),
            "filename": filename,
            "media_type": media_type,
            "size_bytes": _optional_int(value.get("file_size")),
            "duration_seconds": _optional_number(value.get("duration")),
            "width": _optional_int(value.get("width")),
            "height": _optional_int(value.get("height")),
        }
        return {key: item for key, item in descriptor.items() if item is not None}

    async def _resolve_inbound_media(self, inbound: InteractionInbound) -> InteractionInbound | None:
        metadata = dict(inbound.metadata)
        pending = metadata.pop("_telegram_pending_media", [])
        if not pending:
            return inbound
        if not isinstance(pending, list):
            return InteractionInbound(
                channel=inbound.channel,
                text=inbound.text,
                source=inbound.source,
                principal_key=inbound.principal_key,
                reply_to=inbound.reply_to,
                conversation_key=inbound.conversation_key,
                metadata=metadata,
                attachments=list(inbound.attachments),
            )

        attachments = list(inbound.attachments)
        errors: list[str] = []
        bot_token = getattr(self.api, "token", None)
        media_secrets = (
            (
                SecretValue(
                    value=bot_token,
                    name="TELEGRAM_TOKEN",
                    source="channel.telegram.token",
                ),
            )
            if isinstance(bot_token, str) and bot_token
            else ()
        )
        for item in pending:
            if not isinstance(item, dict):
                continue
            try:
                attachment = await asyncio.to_thread(self._download_inbound_media, inbound, item)
            except TelegramInboundMediaError as exc:
                safe_error = redact_exception_message(
                    exc,
                    view=RedactionView.OPERATOR,
                    secrets=media_secrets,
                )
                logger.warning("telegram inbound media skipped: %s", safe_error)
                errors.append(safe_error)
            except Exception as exc:
                safe_error = redact_exception_message(
                    exc,
                    view=RedactionView.OPERATOR,
                    secrets=media_secrets,
                )
                logger.warning(
                    "telegram inbound media download failed: %s",
                    safe_error,
                )
                errors.append(safe_error)
            else:
                attachments.append(attachment)

        if errors:
            metadata["telegram_attachment_errors"] = errors
        if not attachments and metadata.get("telegram_media_only"):
            await self._send_text(
                inbound.source,
                "Telegram attachment could not be downloaded.",
                reply_to=inbound.reply_to,
            )
            return None
        metadata["telegram_attachment_count"] = len(attachments)
        return InteractionInbound(
            channel=inbound.channel,
            text=inbound.text,
            source=inbound.source,
            principal_key=inbound.principal_key,
            reply_to=inbound.reply_to,
            conversation_key=inbound.conversation_key,
            metadata=metadata,
            attachments=attachments,
        )

    def _download_inbound_media(self, inbound: InteractionInbound, media: dict[str, Any]) -> dict[str, Any]:
        file_id = str(media["file_id"])
        file_info = self.api.get_file(file_id)
        file_path = _optional_text(file_info.get("file_path"))
        if not file_path:
            raise TelegramInboundMediaError(f"telegram getFile returned no file_path for {file_id}")
        declared_size = _optional_int(media.get("size_bytes")) or _optional_int(file_info.get("file_size"))
        if declared_size is not None and declared_size > TELEGRAM_INBOUND_MEDIA_MAX_BYTES:
            raise TelegramInboundMediaError(
                f"telegram attachment is too large ({declared_size} bytes, max {TELEGRAM_INBOUND_MEDIA_MAX_BYTES})"
            )
        content = self.api.download_file(file_path)
        if len(content) > TELEGRAM_INBOUND_MEDIA_MAX_BYTES:
            raise TelegramInboundMediaError(
                f"telegram attachment is too large ({len(content)} bytes, max {TELEGRAM_INBOUND_MEDIA_MAX_BYTES})"
            )
        filename = _telegram_attachment_filename(media, file_path=file_path)
        path = self._write_inbound_media_cache(inbound, media, filename=filename, content=content)
        media_type = _telegram_media_type(media, filename=filename)
        attachment: dict[str, Any] = {
            "id": _telegram_attachment_id(inbound, media),
            "kind": _kind_from_media_type(media_type, filename=filename, default=str(media.get("kind") or "document")),
            "filename": filename,
            "media_type": media_type,
            "path": str(path),
            "size_bytes": len(content),
            "source": "telegram",
            "telegram": {
                "chat_id": inbound.metadata.get("telegram_chat_id"),
                "message_id": inbound.metadata.get("telegram_message_id"),
                "file_id": file_id,
                "file_unique_id": media.get("file_unique_id"),
                "file_path": file_path,
                "kind": media.get("telegram_kind"),
            },
        }
        for key in ("duration_seconds", "width", "height"):
            if media.get(key) is not None:
                attachment[key] = media[key]
        return attachment

    def _write_inbound_media_cache(
        self,
        inbound: InteractionInbound,
        media: dict[str, Any],
        *,
        filename: str,
        content: bytes,
    ) -> Path:
        chat = _safe_path_component(str(inbound.metadata.get("telegram_chat_id") or inbound.source or "chat"))
        message = _safe_path_component(str(inbound.metadata.get("telegram_message_id") or inbound.reply_to or "message"))
        digest_source = str(media.get("file_unique_id") or media.get("file_id") or filename)
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
        directory = self.media_cache_root / chat / f"{message}-{digest}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_bytes(content)
        return path

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
        conversation_key = build_conversation_key("telegram", "dm" if chat_type == "private" else "group", chat_id)
        resolution = self._prompt_delivery.consume_callback_data(conversation_key, data)
        if resolution is None:
            return None
        message_id = message.get("message_id")
        return InteractionInbound(
            channel="telegram",
            text=resolution.text,
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
        if not inbound.principal_key:
            inbound.principal_key = inbound.conversation_key or build_conversation_key(
                "telegram",
                "source",
                inbound.source,
            )
        state = self._conversation_state(inbound.conversation_key or build_conversation_key("telegram", "dm", inbound.source))
        self._conversation_lifecycle.remember_route(state, inbound)
        command_outcome = await self._handle_telegram_command(inbound, state)
        if command_outcome.handled:
            return
        inbound = command_outcome.inbound

        inbound = self._conversation_lifecycle.merge_pending(
            state,
            inbound,
            fallback_source=inbound.source,
        )
        await self._conversation_lifecycle.accept_inbound(state, inbound)

    async def deliver(self, outbound: InteractionOutbound) -> None:
        await self._text_outbound_delivery_runtime().deliver(outbound)

    def _text_outbound_delivery_runtime(self) -> TextOutboundDeliveryRuntime:
        return TextOutboundDeliveryRuntime(
            deliver_tool_call=self._deliver_tool_call,
            deliver_tool_results=self._deliver_tool_results,
            deliver_delivery=self._deliver_delivery,
            prompt_user=self.prompt_user,
        )

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        delivery = self._prompt_delivery.prepare(prompt)
        if delivery is None:
            return ""
        await self._send_text(
            delivery.source,
            delivery.text,
            reply_to=delivery.reply_to,
            reply_markup={"inline_keyboard": choice_button_rows(delivery.choices)} if delivery.choices else None,
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

        conversation_key = inbound.conversation_key or build_conversation_key("telegram", "dm", inbound.source)
        payload = TelegramPendingApproval(
            source=inbound.source,
            reply_to=inbound.reply_to,
            conversation_key=conversation_key,
        )
        pending = self._pending_approvals.open(request, payload=payload)
        send_task = asyncio.create_task(
            self._send_text(
                inbound.source,
                format_approval_request_text(request),
                reply_to=inbound.reply_to,
                reply_markup={"inline_keyboard": approval_button_rows(pending.approval_id)},
            )
        )
        try:
            sent = await asyncio.shield(send_task)
        except asyncio.CancelledError:
            try:
                sent = await send_task
            except BaseException:
                self._pending_approvals.discard(pending.approval_id)
                payload.message_ready.set()
            else:
                self._record_pending_approval_message(pending, sent, conversation_key)
                resolved = self._resolve_pending_approval(
                    pending.approval_id,
                    ApprovalDecision("deny", "telegram approval cancelled"),
                )
                if resolved is not None:
                    await self._edit_approval_message(
                        resolved,
                        "Approval expired",
                        "The turn was stopped before approval.",
                    )
            raise
        except BaseException:
            self._pending_approvals.discard(pending.approval_id)
            payload.message_ready.set()
            raise
        self._record_pending_approval_message(pending, sent, conversation_key)
        try:
            return await asyncio.wait_for(
                self._pending_approvals.wait(pending, shield=True),
                timeout=self.approval_timeout_seconds,
            )
        except asyncio.TimeoutError:
            decision = ApprovalDecision("deny", "telegram approval timed out")
            resolved = self._resolve_pending_approval(pending.approval_id, decision)
            if resolved is not None:
                await self._edit_approval_message(resolved, "Approval expired", "Request denied after 10 minutes.")
            else:
                await self._send_text(inbound.source, "Approval timed out; request denied.", reply_to=inbound.reply_to)
            return decision
        except asyncio.CancelledError:
            resolved = self._resolve_pending_approval(
                pending.approval_id,
                ApprovalDecision("deny", "telegram approval cancelled"),
            )
            if resolved is not None:
                await self._edit_approval_message(resolved, "Approval expired", "The turn was stopped before approval.")
            raise

    async def _deliver_tool_results(self, records, *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet" or not records:
            return
        target = text_outbound_target(outbound)
        if target is None:
            return
        text = self._tool_results_text(records)
        if text:
            await self._send_text(target.source, text, reply_to=target.reply_to)

    async def _deliver_tool_call(self, record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet":
            return
        target = text_outbound_target(outbound)
        if target is None:
            return
        text = self._tool_call_text(record)
        if not text:
            return
        key = self._tool_message_key(record, outbound)
        if record.phase == "finish":
            edit_target = self._tool_message_ids.pop(key, None)
            if edit_target is not None and await self._edit_tool_call_message(edit_target[0], edit_target[1], text):
                return
        sent = await self._send_text(target.source, text, reply_to=target.reply_to)
        message_id = _telegram_message_id(sent)
        if record.phase == "start" and message_id is not None:
            self._tool_message_ids[key] = (target.source, message_id)

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
        return tool_call_markdown(record, mode=self.tool_display)

    def _tool_results_text(self, records) -> str:
        return tool_results_markdown(records, mode=self.tool_display)

    async def _deliver_delivery(self, delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> None:
        if not delivery.visible:
            return
        target = text_outbound_target(outbound)
        if target is None:
            return
        await self._native_delivery_runtime().deliver(delivery, target=target)

    def _native_delivery_runtime(self) -> NativeDeliveryRuntime:
        return NativeDeliveryRuntime(
            send_text=self._send_text,
            send_media=self._send_native_media,
        )

    async def _send_native_media(
        self,
        request: NativeMediaRequest,
        *,
        target: TextOutboundTarget,
        reply_to: str | None = None,
    ) -> bool:
        reply_to_id = int(reply_to) if isinstance(reply_to, str) and reply_to.isdigit() else None
        try:
            if request.kind == "image":
                await asyncio.to_thread(
                    self.api.send_photo,
                    chat_id=target.source,
                    photo=request.source,
                    caption=request.caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            elif request.kind == "audio":
                await self._deliver_voice_block(
                    chat_id=target.source,
                    source=request.source,
                    caption=request.caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            elif request.kind == "video":
                await asyncio.to_thread(
                    self.api.send_video,
                    chat_id=target.source,
                    video=request.source,
                    caption=request.caption,
                    reply_to_message_id=reply_to_id if _should_thread_reply(reply_to_id, 0, self.reply_to_mode) else None,
                )
            else:
                await asyncio.to_thread(
                    self.api.send_document,
                    chat_id=target.source,
                    document=request.source,
                    caption=request.caption,
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

    def _conversation_state(self, conversation_key: str) -> TelegramConversationState:
        return self._conversation_lifecycle.state_for_key(conversation_key)

    def _new_conversation_state(self, conversation_key: str) -> TelegramConversationState:
        return TelegramConversationState(
            runtime=self._runtime_factory(conversation_key),
            busy_mode=self.default_busy_mode,
            route_binding=SessionRouteBinding(route=self),
            conversation_key=conversation_key,
        )

    async def _notify_busy_inbound(
        self,
        state: TelegramConversationState,
        inbound: InteractionInbound,
        decision: BusyInboundDecision,
    ) -> None:
        if decision.kind == "queue":
            await self._send_text(inbound.source, f"Queued for next turn: {self._shorten(inbound.text)}", reply_to=inbound.reply_to)
            return
        if decision.kind == "interrupt":
            await self._send_text(
                inbound.source,
                f"Interrupting current turn; queued latest input: {self._shorten(inbound.text)}",
                reply_to=inbound.reply_to,
            )

    async def _run_inbound(self, state: TelegramConversationState, inbound: InteractionInbound) -> None:
        token = self._active_inbound.set(inbound)
        try:
            if self.send_typing:
                await self._send_typing(inbound.source)
            outbound = await state.runtime.handle(inbound, route_binding=state.route_binding)
            await self.deliver(outbound)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            secrets: list[SecretValue] = []
            provider_api_key = getattr(
                getattr(
                    getattr(state.runtime, "runner", None),
                    "provider",
                    None,
                ),
                "api_key",
                None,
            )
            if isinstance(provider_api_key, str) and provider_api_key:
                secrets.append(
                    SecretValue(
                        value=provider_api_key,
                        name="API_KEY",
                        source="provider.api_key",
                    )
                )
            bot_token = getattr(self.api, "token", None)
            if isinstance(bot_token, str) and bot_token:
                secrets.append(
                    SecretValue(
                        value=bot_token,
                        name="TELEGRAM_TOKEN",
                        source="channel.telegram.token",
                    )
                )
            safe_error = redact_exception(
                exc,
                view=RedactionView.OPERATOR,
                secrets=secrets,
            )
            logger.error("telegram turn failed: %s", safe_error)
            await self._send_text(
                inbound.source,
                f"Turn failed: {safe_error}",
                reply_to=inbound.reply_to,
            )
        finally:
            self._active_inbound.reset(token)

    async def _handle_telegram_command(
        self,
        inbound: InteractionInbound,
        state: TelegramConversationState,
    ):
        async def send_notice(text: str) -> None:
            await self._send_command_text(inbound, text)

        return await self._command_runtime.handle(
            inbound,
            state,
            handlers=self._command_executor.handlers(),
            send_notice=send_notice,
        )

    async def _send_command_text(self, inbound: InteractionInbound, text: str) -> None:
        await self._send_text(inbound.source, text, reply_to=inbound.reply_to)

    def _status_extra_lines(self, inbound: InteractionInbound) -> tuple[str, ...]:
        return (
            "- access: `restricted`",
            f"- allowed users: `{len(self.allowed_users)}`",
            f"- allowed chats: `{len(self.allowed_chats)}`",
            f"- current authorized: `{str(self._inbound_authorized(inbound)).lower()}`",
        )

    async def _cancel_active(self, state: TelegramConversationState) -> None:
        await self._conversation_lifecycle.cancel_active(
            state,
            before_cancel=lambda: self._cancel_pending_approval(state, reason="telegram turn stopped")
        )

    def _state_for_session(self, session_id: str) -> TelegramConversationState | None:
        return self._conversation_lifecycle.state_for_session(session_id)

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

    async def _handle_approval_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        data = str(callback.get("data") or "")
        parsed = parse_approval_callback_data(data)
        if parsed is None:
            if callback_id:
                await self._answer_callback_query(str(callback_id), text="Invalid approval action.")
            return
        decision = approval_decision_for_action(parsed.action, actor="Telegram user")
        if decision is None:
            if callback_id:
                await self._answer_callback_query(str(callback_id), text="Invalid approval action.")
            return
        pending = self._pending_approvals.get(parsed.approval_id)
        if pending is None:
            if callback_id:
                await self._answer_callback_query(str(callback_id), text="Approval expired.")
            await self._edit_expired_callback_message(callback)
            return
        if callback_id:
            await self._answer_callback_query(str(callback_id), text=approval_callback_answer(decision))
        resolution = approval_resolution(parsed.action)
        if resolution is not None:
            await self._edit_approval_message(pending, resolution.title, resolution.detail)
        self._resolve_pending_approval(parsed.approval_id, decision)

    def _resolve_pending_approval(self, approval_id: str, decision: ApprovalDecision) -> PendingApproval | None:
        pending = self._pending_approvals.resolve(approval_id, decision)
        if pending is None:
            return None
        payload: TelegramPendingApproval = pending.payload
        state = self._conversations.get(payload.conversation_key)
        if state is not None and state.pending_approval_id == approval_id:
            state.pending_approval_id = None
        return pending

    def _record_pending_approval_message(
        self,
        pending: PendingApproval,
        sent: dict[str, Any] | None,
        conversation_key: str,
    ) -> None:
        payload: TelegramPendingApproval = pending.payload
        payload.message_id = _telegram_message_id(sent)
        payload.message_ready.set()
        self._conversation_state(conversation_key).pending_approval_id = pending.approval_id

    async def _cancel_pending_approval(self, state: TelegramConversationState, *, reason: str) -> None:
        approval_id = state.pending_approval_id
        if approval_id is None:
            return
        pending = self._resolve_pending_approval(approval_id, ApprovalDecision("deny", reason))
        if pending is not None:
            await self._edit_approval_message(pending, "Approval expired", "The turn was stopped before approval.")

    async def _edit_approval_message(self, pending: PendingApproval, title: str, detail: str) -> None:
        payload: TelegramPendingApproval = pending.payload
        if payload.message_id is None:
            await payload.message_ready.wait()
        if payload.message_id is None or not hasattr(self.api, "edit_message_text"):
            return
        text = format_resolved_approval_text(pending.request, title=title, detail=detail)
        formatted = format_telegram_markdown_v2(text)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                self.api.edit_message_text,
                chat_id=payload.source,
                message_id=payload.message_id,
                text=formatted,
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
        if hasattr(self.api, "edit_message_reply_markup"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.api.edit_message_reply_markup,
                    chat_id=payload.source,
                    message_id=payload.message_id,
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
    workspace = getattr(getattr(app, "workspace", None), "root", None)
    return TelegramInteractionBridge.from_config(
        None,
        config,
        runtime_factory=runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
        workspace_root=workspace,
    )


def _resolve_media_cache_root(
    *,
    workspace_root: str | Path | None,
    media_cache_root: str | Path | None,
) -> Path:
    if media_cache_root is not None:
        return Path(media_cache_root).expanduser().resolve()
    if workspace_root is not None:
        return (Path(workspace_root).expanduser().resolve() / TELEGRAM_INBOUND_MEDIA_CACHE_DIR).resolve()
    return (Path.cwd() / TELEGRAM_INBOUND_MEDIA_CACHE_DIR).resolve()


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


def _telegram_photo_score(item: dict[str, Any]) -> int:
    size = _optional_int(item.get("file_size"))
    if size is not None:
        return size
    width = _optional_int(item.get("width")) or 0
    height = _optional_int(item.get("height")) or 0
    return width * height


def _telegram_attachment_filename(media: dict[str, Any], *, file_path: str) -> str:
    raw = _optional_text(media.get("filename")) or Path(file_path).name
    extension = _telegram_extension(media, raw_filename=raw, file_path=file_path)
    fallback = f"{media.get('telegram_kind') or media.get('kind') or 'attachment'}{extension}"
    filename = _safe_filename(raw or fallback)
    if not Path(filename).suffix:
        filename = f"{filename}{extension}"
    return filename


def _telegram_extension(media: dict[str, Any], *, raw_filename: str | None, file_path: str) -> str:
    for candidate in (raw_filename, file_path):
        if candidate:
            suffix = Path(candidate).suffix.lower()
            if suffix and len(suffix) <= 12:
                return suffix
    media_type = _optional_text(media.get("media_type"))
    if media_type and media_type.lower() in _TELEGRAM_MIME_EXTENSIONS:
        return _TELEGRAM_MIME_EXTENSIONS[media_type.lower()]
    if media_type:
        guessed = mimetypes.guess_extension(media_type)
        if guessed:
            return guessed
    return _TELEGRAM_DEFAULT_EXTENSIONS.get(str(media.get("kind") or "document"), ".bin")


def _telegram_media_type(media: dict[str, Any], *, filename: str) -> str:
    media_type = _optional_text(media.get("media_type"))
    if media_type:
        return media_type
    guessed = mimetypes.guess_type(filename)[0]
    if guessed:
        return guessed
    return _TELEGRAM_DEFAULT_MEDIA_TYPES.get(str(media.get("kind") or "document"), "application/octet-stream")


def _kind_from_media_type(media_type: str | None, *, filename: str | None = None, default: str = "document") -> str:
    normalized = (media_type or mimetypes.guess_type(filename or "")[0] or "").lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("video/"):
        return "video"
    return default


def _telegram_attachment_id(inbound: InteractionInbound, media: dict[str, Any]) -> str:
    chat = inbound.metadata.get("telegram_chat_id") or inbound.source
    message = inbound.metadata.get("telegram_message_id") or inbound.reply_to
    digest_source = str(media.get("file_unique_id") or media.get("file_id") or "")
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    return f"telegram:{chat}:{message}:{digest}"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _safe_filename(value: str) -> str:
    filename = Path(value).name
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    return filename or "attachment.bin"


def _safe_path_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "unknown"


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
