import asyncio
import contextlib
import io
import json
import shutil
import urllib.error

import pytest
import yaml
from pydantic import ValidationError

from demiurge.security.approval import ApprovalRequest
from demiurge import cli
from demiurge.app import create_app, source_agents_root
from demiurge.channels.gateway import build_enabled_gateway_channels
from demiurge.channels.telegram import (
    TelegramApiError,
    TelegramBotApi,
    TelegramInteractionBridge,
    _should_thread_reply,
    format_telegram_markdown_v2,
    split_telegram_message,
    utf16_len,
)
from demiurge.core import CoreLoader, TelegramChannelConfig
from demiurge.runtime.interactions import (
    BridgeApprovalProvider,
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    InteractionOutbound,
    InteractionRuntime,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.providers import ToolCall
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord
from demiurge.util import write_json


def _outbound(
    channel: str = "telegram",
    *,
    items=None,
    delivery_list=None,
    tool_result_list=None,
    prompt=None,
    session_id=None,
    turn_id=None,
    metadata=None,
):
    outbound_items = list(items or [])
    outbound_items.extend(InteractionItem.tool_result_item(record) for record in tool_result_list or [])
    outbound_items.extend(InteractionItem.delivery_item(delivery) for delivery in delivery_list or [])
    return InteractionOutbound(
        channel=channel,
        items=outbound_items,
        prompt=prompt,
        session_id=session_id,
        turn_id=turn_id,
        metadata=metadata,
    )


class FakeRunner:
    core_id = "assistant"

    def __init__(self):
        self.texts = []
        self.kwargs = {}

    async def run_turn(self, text, **kwargs):
        self.texts.append(text)

        class Result:
            session_id = "session_1"
            turn_id = "turn_1"
            needs_user = False
            tool_results = []

        result = Result()
        result.deliveries = [InteractionDelivery(type="text", text=f"echo: {text}")]
        result.items = [InteractionItem.delivery_item(delivery) for delivery in result.deliveries]
        self.kwargs = kwargs
        return result


class BlockingRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def run_turn(self, text, **kwargs):
        self.texts.append(text)
        if len(self.texts) == 1:
            self.first_started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        else:
            self.second_started.set()

        class Result:
            session_id = "session_1"
            turn_id = "turn_1"
            needs_user = False
            tool_results = []

        result = Result()
        result.deliveries = [InteractionDelivery(type="text", text=f"echo: {text}")]
        result.items = [InteractionItem.delivery_item(delivery) for delivery in result.deliveries]
        self.kwargs = kwargs
        return result


class ApprovalRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.request_started = asyncio.Event()
        self.decision = None

    async def run_turn(self, text, **kwargs):
        self.texts.append(text)
        self.kwargs = kwargs
        request = ApprovalRequest(
            tool_name="terminal",
            tool_call_id="call_1",
            turn_id="turn_1",
            capability="terminal.exec",
            action="exec",
            risk="critical",
            summary="Run terminal command in .",
            target=".",
            command="whoami",
            arguments_preview={"cwd": ".", "command": "whoami", "env_keys": []},
        )
        self.request_started.set()
        self.decision = await BridgeApprovalProvider().decide(request)

        class Result:
            session_id = "session_1"
            turn_id = "turn_1"
            needs_user = False
            tool_results = []

        result = Result()
        result.deliveries = [InteractionDelivery(type="text", text=f"decision: {self.decision.value}")]
        result.items = [InteractionItem.delivery_item(delivery) for delivery in result.deliveries]
        return result


class FakeApi:
    def __init__(self):
        self.sent = []
        self.rich_sent = []
        self.rich_attempts = 0
        self.actions = []
        self.callbacks = []
        self.commands = []
        self.edits = []
        self.reply_markup_edits = []
        self.media_sent = []
        self.voice_sent = []
        self.next_message_id = 1000
        self.fail_markdown = False
        self.fail_rich: Exception | None = None
        self.fail_media = False

    def send_message(self, *, chat_id, text, reply_to_message_id=None, parse_mode=None, reply_markup=None):
        if self.fail_markdown and parse_mode == "MarkdownV2":
            raise RuntimeError("Bad Request: can't parse entities")
        message_id = self.next_message_id
        self.next_message_id += 1
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return {"ok": True, "result": {"message_id": message_id}}

    def edit_message_text(self, *, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return {"ok": True, "result": {"message_id": message_id}}

    def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup=None):
        self.reply_markup_edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            }
        )
        return {"ok": True, "result": {"message_id": message_id}}

    def send_rich_message(self, *, chat_id, markdown, reply_to_message_id=None):
        self.rich_attempts += 1
        if self.fail_rich is not None:
            raise self.fail_rich
        self.rich_sent.append(
            {
                "chat_id": chat_id,
                "markdown": markdown,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"ok": True, "result": {"message_id": 99}}

    def send_photo(self, *, chat_id, photo, caption=None, reply_to_message_id=None):
        return self._send_media("photo", chat_id=chat_id, value=photo, caption=caption, reply_to_message_id=reply_to_message_id)

    def send_audio(self, *, chat_id, audio, caption=None, reply_to_message_id=None):
        return self._send_media("audio", chat_id=chat_id, value=audio, caption=caption, reply_to_message_id=reply_to_message_id)

    def send_voice(self, *, chat_id, voice, caption=None, reply_to_message_id=None):
        if self.fail_media:
            raise RuntimeError("media failed")
        self.voice_sent.append(
            {
                "chat_id": chat_id,
                "value": voice,
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"ok": True}

    def send_video(self, *, chat_id, video, caption=None, reply_to_message_id=None):
        return self._send_media("video", chat_id=chat_id, value=video, caption=caption, reply_to_message_id=reply_to_message_id)

    def send_document(self, *, chat_id, document, caption=None, reply_to_message_id=None):
        return self._send_media("document", chat_id=chat_id, value=document, caption=caption, reply_to_message_id=reply_to_message_id)

    def _send_media(self, kind, *, chat_id, value, caption=None, reply_to_message_id=None):
        if self.fail_media:
            raise RuntimeError("media failed")
        self.media_sent.append(
            {
                "kind": kind,
                "chat_id": chat_id,
                "value": value,
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"ok": True}

    def send_chat_action(self, *, chat_id, action="typing"):
        self.actions.append({"chat_id": chat_id, "action": action})
        return {"ok": True}

    def answer_callback_query(self, *, callback_query_id, text=None):
        self.callbacks.append({"callback_query_id": callback_query_id, "text": text})
        return {"ok": True}

    def set_my_commands(self, commands):
        self.commands.append(commands)
        return {"ok": True}


class PollingFakeApi(FakeApi):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.get_updates_calls = []
        self.delete_webhook_calls = []

    def delete_webhook(self, *, drop_pending_updates=False):
        self.delete_webhook_calls.append({"drop_pending_updates": drop_pending_updates})
        return {"ok": True, "result": True}

    def get_updates(self, *, offset=None, timeout=30):
        self.get_updates_calls.append({"offset": offset, "timeout": timeout})
        if not self.outcomes:
            return []
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_telegram_bot_api_send_audio_uploads_local_file(tmp_path, monkeypatch):
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"MP3DATA")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 123}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = TelegramBotApi("token").send_audio(chat_id=42, audio=str(audio_path), caption="voice")

    request = captured["request"]
    body = request.data
    assert result["ok"] is True
    assert request.full_url == "https://api.telegram.org/bottoken/sendAudio"
    assert request.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in body
    assert b"42" in body
    assert b'name="caption"' in body
    assert b"voice" in body
    assert b'name="audio"; filename="voice.mp3"' in body
    assert b"Content-Type: audio/mpeg" in body
    assert b"MP3DATA" in body


def test_telegram_bot_api_send_voice_uploads_local_file(tmp_path, monkeypatch):
    voice_path = tmp_path / "voice.ogg"
    voice_path.write_bytes(b"OGGDATA")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 123}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = TelegramBotApi("token").send_voice(chat_id=42, voice=str(voice_path), caption="voice")

    request = captured["request"]
    body = request.data
    assert result["ok"] is True
    assert request.full_url == "https://api.telegram.org/bottoken/sendVoice"
    assert request.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="voice"; filename="voice.ogg"' in body
    assert b"Content-Type: audio/ogg" in body
    assert b"OGGDATA" in body


def test_telegram_bot_api_retries_transient_urlopen_error(monkeypatch):
    attempts = []
    sleeps = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": []}).encode("utf-8")

    def fake_urlopen(request, timeout):
        attempts.append({"request": request, "timeout": timeout})
        if len(attempts) == 1:
            raise urllib.error.URLError("temporary reset")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr("random.uniform", lambda start, end: 0)

    result = TelegramBotApi("token").get_updates(timeout=1)

    assert result == []
    assert len(attempts) == 2
    assert attempts[0]["request"].full_url == "https://api.telegram.org/bottoken/getUpdates"
    assert sleeps == [0.5]


def test_telegram_bot_api_does_not_retry_http_error(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(request)
        raise urllib.error.HTTPError(request.full_url, 502, "bad gateway", hdrs=None, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: (_ for _ in ()).throw(AssertionError("should not sleep")))

    with pytest.raises(urllib.error.HTTPError):
        TelegramBotApi("token").get_updates(timeout=1)

    assert len(attempts) == 1


def test_telegram_bot_api_raises_structured_api_error(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 3",
                    "parameters": {"retry_after": 3},
                }
            ).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())

    with pytest.raises(TelegramApiError) as exc_info:
        TelegramBotApi("token").get_updates(timeout=1)

    error = exc_info.value
    assert error.method == "getUpdates"
    assert error.error_code == 429
    assert error.retry_after == 3.0
    assert "Too Many Requests" in error.description


def test_telegram_bot_api_converts_http_error_body_to_api_error(monkeypatch):
    payload = json.dumps(
        {
            "ok": False,
            "error_code": 409,
            "description": "Conflict: terminated by other getUpdates request",
        }
    ).encode("utf-8")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 409, "Conflict", hdrs=None, fp=io.BytesIO(payload))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(TelegramApiError) as exc_info:
        TelegramBotApi("token").get_updates(timeout=1)

    error = exc_info.value
    assert error.method == "getUpdates"
    assert error.error_code == 409
    assert "Conflict" in error.description


def test_telegram_bot_api_delete_webhook_wire_shape():
    class RecordingApi(TelegramBotApi):
        def __init__(self):
            super().__init__("token")
            self.calls = []

        def _request(self, method, params, *, retry_policy=None):
            self.calls.append((method, params, retry_policy))
            return {"ok": True, "result": True}

    api = RecordingApi()

    result = api.delete_webhook(drop_pending_updates=False)

    assert result["ok"] is True
    assert api.calls == [("deleteWebhook", {"drop_pending_updates": "false"}, "safe")]


def _message(text, *, chat_type="private", chat_id=1, user_id=42, message_id=10, **extra):
    message = {
        "message_id": message_id,
        "from": {"id": user_id},
        "chat": {"id": chat_id, "type": chat_type},
        "text": text,
    }
    message.update(extra)
    return {"update_id": 100, "message": message}


def _callback(data, *, chat_id=1, user_id=42, message_id=10, callback_id="cb_1", chat_type="private"):
    return {
        "update_id": 101,
        "callback_query": {
            "id": callback_id,
            "from": {"id": user_id},
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": chat_type}},
        },
    }


async def _wait_until(predicate, *, timeout=1):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition was not met")
        await asyncio.sleep(0.01)


class FakeVersionStore:
    def __init__(self, core_path):
        self.core_path = core_path

    def active_core_path(self, core_id):
        return self.core_path


def test_telegram_bot_api_send_rich_message_wire_shape():
    class RecordingApi(TelegramBotApi):
        def __init__(self):
            super().__init__("token")
            self.calls = []

        def _request(self, method, params):
            self.calls.append((method, params))
            return {"ok": True}

    api = RecordingApi()

    api.send_rich_message(chat_id="123", markdown="| A | B |", reply_to_message_id=456)
    api.edit_message_text(chat_id="123", message_id=99, text="done", parse_mode="MarkdownV2", reply_markup=None)
    api.edit_message_reply_markup(chat_id="123", message_id=99, reply_markup=None)

    assert api.calls == [
        (
            "sendRichMessage",
            {
                "chat_id": "123",
                "rich_message": '{"markdown": "| A | B |"}',
                "reply_parameters": '{"message_id": 456}',
            },
        ),
        (
            "editMessageText",
            {
                "chat_id": "123",
                "message_id": 99,
                "text": "done",
                "parse_mode": "MarkdownV2",
            },
        ),
        (
            "editMessageReplyMarkup",
            {
                "chat_id": "123",
                "message_id": 99,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_interaction_runtime_calls_runner_with_metadata():
    runner = FakeRunner()
    runtime = InteractionRuntime(runner)

    outbound = await runtime.handle(
        InteractionInbound(
            channel="test",
            text="hello",
            source="source",
            reply_to="reply",
            conversation_key="conversation",
            metadata={"k": "v"},
        )
    )

    assert [delivery.text for delivery in outbound.deliveries] == ["echo: hello"]
    assert runner.kwargs["interaction"].channel == "test"
    assert runner.kwargs["interaction"].source == "source"
    assert runner.kwargs["interaction"].reply_to == "reply"
    assert runner.kwargs["interaction"].conversation_key == "conversation"
    assert runner.kwargs["interaction"].metadata == {"k": "v"}


@pytest.mark.asyncio
async def test_bridge_approval_provider_fails_closed_without_bridge():
    request = ApprovalRequest(
        tool_name="terminal",
        tool_call_id="call_1",
        turn_id="turn_1",
        capability="terminal.exec",
        action="exec",
        risk="high",
        summary="run",
    )

    decision = await BridgeApprovalProvider().decide(request)

    assert decision.value == "deny"
    assert "no active interaction bridge" in decision.reason


def test_telegram_normalizes_private_group_command_mention_and_reply():
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=FakeApi(), bot_username="demiurge_bot")

    private = bridge.normalize_update(_message("hello", chat_type="private"))
    ignored = bridge.normalize_update(_message("hello group", chat_type="group"))
    command = bridge.normalize_update(_message("/ask explain this", chat_type="group"))
    command_at = bridge.normalize_update(_message("/ask@demiurge_bot explain that", chat_type="group"))
    mention = bridge.normalize_update(_message("@demiurge_bot summarize", chat_type="supergroup"))
    reply = bridge.normalize_update(
        _message(
            "continue",
            chat_type="group",
            reply_to_message={"from": {"is_bot": True, "username": "demiurge_bot"}},
        )
    )

    assert private.text == "hello"
    assert private.reply_to == "10"
    assert private.conversation_key == "telegram:1"
    assert ignored is None
    assert command.text == "explain this"
    assert command_at.text == "explain that"
    assert mention.text == "summarize"
    assert reply.text == "continue"
    assert private.metadata["telegram_user_id"] == 42
    assert private.metadata["telegram_chat_id"] == 1


@pytest.mark.asyncio
async def test_telegram_access_policy_denies_private_without_allowlist():
    runner = FakeRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(runner), api=api, bot_username="demiurge_bot")

    await bridge.handle_update(_message("hello", chat_id=123, user_id=42))

    assert runner.texts == []
    assert bridge._conversations == {}
    assert api.sent[-1]["chat_id"] == "123"
    assert "access denied" in api.sent[-1]["text"].lower()


@pytest.mark.asyncio
async def test_telegram_access_policy_allows_private_user():
    runner = FakeRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.handle_update(_message("hello", chat_id=123, user_id=42))
    state = bridge._conversations["telegram:123"]
    await state.active_task

    assert runner.texts == ["hello"]


@pytest.mark.asyncio
async def test_telegram_run_forever_recovers_from_transient_polling_error():
    runner = FakeRunner()
    api = PollingFakeApi([urllib.error.URLError("temporary reset"), [_message("hello", chat_id=123, user_id=42)]])
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )
    bridge._polling_network_base_delay = 0
    bridge._polling_network_max_delay = 0

    task = asyncio.create_task(bridge.run_forever())
    try:
        await _wait_until(lambda: runner.texts == ["hello"])
        state = bridge._conversations["telegram:123"]
        if state.active_task is not None:
            await state.active_task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert api.delete_webhook_calls == [{"drop_pending_updates": False}]
    assert len(api.get_updates_calls) >= 2
    assert bridge._polling_network_error_count == 0


@pytest.mark.asyncio
async def test_telegram_run_forever_waits_out_polling_conflict():
    runner = FakeRunner()
    api = PollingFakeApi(
        [
            TelegramApiError("getUpdates", 409, "Conflict: terminated by other getUpdates request"),
            [_message("hello", chat_id=123, user_id=42)],
        ]
    )
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )
    bridge._polling_conflict_base_delay = 0
    bridge._polling_conflict_step_delay = 0

    task = asyncio.create_task(bridge.run_forever())
    try:
        await _wait_until(lambda: runner.texts == ["hello"])
        state = bridge._conversations["telegram:123"]
        if state.active_task is not None:
            await state.active_task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(api.get_updates_calls) >= 2
    assert bridge._polling_conflict_count == 0


@pytest.mark.asyncio
async def test_telegram_run_forever_honors_retry_after(monkeypatch):
    runner = FakeRunner()
    api = PollingFakeApi(
        [
            TelegramApiError("getUpdates", 429, "Too Many Requests", {"retry_after": 2.5}),
            [_message("hello", chat_id=123, user_id=42)],
        ]
    )
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("demiurge.channels.telegram.bridge.asyncio.sleep", fake_sleep)

    task = asyncio.create_task(bridge.run_forever())
    try:
        await _wait_until(lambda: runner.texts == ["hello"])
        state = bridge._conversations["telegram:123"]
        if state.active_task is not None:
            await state.active_task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert 2.5 in sleeps
    assert len(api.get_updates_calls) >= 2


@pytest.mark.asyncio
async def test_telegram_access_policy_group_requires_allowed_chat_user_and_mention():
    runner = FakeRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
        allowed_chats=[-100],
    )

    await bridge.handle_update(_message("hello", chat_type="supergroup", chat_id=-100, user_id=42))
    assert runner.texts == []
    assert bridge._conversations == {}

    await bridge.handle_update(_message("@demiurge_bot summarize", chat_type="supergroup", chat_id=-100, user_id=42))
    state = bridge._conversations["telegram:-100"]
    await state.active_task

    assert runner.texts == ["summarize"]


@pytest.mark.asyncio
async def test_telegram_access_policy_group_rejects_without_chat_or_user_match():
    api = FakeApi()
    only_chat = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        allowed_chats=[-100],
    )
    await only_chat.handle_update(_message("@demiurge_bot summarize", chat_type="group", chat_id=-100, user_id=42))
    assert only_chat._conversations == {}

    only_user = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )
    await only_user.handle_update(_message("@demiurge_bot summarize", chat_type="group", chat_id=-100, user_id=42))
    assert only_user._conversations == {}


@pytest.mark.asyncio
async def test_telegram_access_policy_unauthorized_choice_callback_does_not_consume_choice():
    runner = FakeRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )
    await bridge.deliver(
        _outbound(
            channel="telegram",
            prompt=UserPromptRequest(
                question="Which path?",
                choices=["fast", "careful"],
                conversation_key="telegram:123",
                metadata={"source": "123", "reply_to": "456"},
            ),
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    await bridge.handle_update(_callback("choice:1", chat_id=123, user_id=99, message_id=456, callback_id="cb_bad"))

    assert bridge._pending_choices["telegram:123"] == ["fast", "careful"]
    assert runner.texts == []
    assert api.callbacks[-1] == {"callback_query_id": "cb_bad", "text": "Telegram access denied."}


@pytest.mark.asyncio
async def test_telegram_access_policy_unauthorized_approval_callback_does_not_resolve():
    runner = ApprovalRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="run",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
            metadata={"telegram_chat_type": "private"},
        )
    )
    await asyncio.wait_for(runner.request_started.wait(), timeout=1)
    await _wait_until(lambda: bool(bridge._pending_approvals), timeout=5)

    await bridge.handle_update(_callback("approval:1:allow", chat_id=123, user_id=99, message_id=456, callback_id="cb_bad"))

    assert "1" in bridge._pending_approvals
    assert runner.decision is None
    assert api.callbacks[-1] == {"callback_query_id": "cb_bad", "text": "Telegram access denied."}
    state = bridge._conversations["telegram:123"]
    await bridge._cancel_active(state)


def test_telegram_bridge_from_config_prefers_env_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TEST_TOKEN", "env-token")
    config = TelegramChannelConfig(
        bot_token_env="TELEGRAM_TEST_TOKEN",
        bot_token="inline-token",
        bot_username="@demiurge_bot",
        allowed_users=[42],
        allowed_chats=[-100],
        unauthorized_response="silent",
        poll_timeout=7,
    )

    bridge = TelegramInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)

    assert bridge.api.token == "env-token"
    assert bridge.bot_username == "demiurge_bot"
    assert bridge.poll_timeout == 7
    assert bridge.message_format == "markdown_v2"
    assert bridge.rich_messages is True
    assert bridge.default_busy_mode == "interrupt"
    assert bridge.allowed_users == {42}
    assert bridge.allowed_chats == {-100}
    assert bridge.unauthorized_response == "silent"


def test_telegram_bridge_from_config_uses_inline_token_when_env_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_MISSING_TOKEN", raising=False)
    config = TelegramChannelConfig(bot_token_env="TELEGRAM_MISSING_TOKEN", bot_token="inline-token")

    bridge = TelegramInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)

    assert bridge.api.token == "inline-token"


def test_telegram_bridge_from_config_requires_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_MISSING_TOKEN", raising=False)
    config = TelegramChannelConfig(bot_token_env="TELEGRAM_MISSING_TOKEN")

    with pytest.raises(RuntimeError, match="bot_token_env with a value or bot_token"):
        TelegramInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)


def test_build_enabled_gateway_channels_uses_enabled_telegram_config(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"

    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["channels"]["telegram"]["enabled"] = True
    raw["channels"]["telegram"]["bot_token_env"] = "TELEGRAM_MISSING_TOKEN"
    raw["channels"]["telegram"]["bot_token"] = "inline-token"
    raw["channels"]["telegram"]["allowed_users"] = [42]
    raw["channels"]["telegram"]["allowed_chats"] = [-100]
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app = type(
        "FakeApp",
        (),
        {
            "core_loader": CoreLoader(),
            "version_store": FakeVersionStore(target),
            "runner": FakeRunner(),
            "tool_display": "full",
            "channel_busy_mode": "queue",
        },
    )()

    channels = build_enabled_gateway_channels(app)
    bridge = channels[0].bridge

    assert [channel.name for channel in channels] == ["telegram"]
    assert bridge.api.token == "inline-token"
    assert bridge.tool_display == "full"
    assert bridge.default_busy_mode == "queue"
    assert bridge.allowed_users == {42}
    assert bridge.allowed_chats == {-100}


def test_telegram_markdown_formatter_and_utf16_chunking():
    formatted = format_telegram_markdown_v2(
        "## Title\n**bold** and `snake_case`\n[docs](https://example.com/a_(b))\n\n| Name | Value |\n| --- | --- |\n| A | B |"
    )

    assert "*Title*" in formatted
    assert "*bold*" in formatted
    assert "`snake_case`" in formatted
    assert "[docs](https://example.com/a_(b\\))" in formatted
    assert "\\| --- \\|" not in formatted
    assert "\\- Name: A" not in formatted

    chunks = split_telegram_message("😀" * 3000, markdown_v2=True)
    assert len(chunks) > 1
    assert all(utf16_len(chunk) <= 4096 for chunk in chunks)


def test_build_enabled_gateway_channels_fails_without_enabled_channel(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"

    shutil.copytree(source, target)
    app = type(
        "FakeApp",
        (),
        {"core_loader": CoreLoader(), "version_store": FakeVersionStore(target), "runner": FakeRunner()},
    )()

    with pytest.raises(RuntimeError, match="no enabled gateway channels"):
        build_enabled_gateway_channels(app)


def test_build_enabled_gateway_channels_fails_when_enabled_telegram_has_no_token(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"

    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["channels"]["telegram"]["enabled"] = True
    raw["channels"]["telegram"]["bot_token_env"] = "TELEGRAM_MISSING_TOKEN"
    raw["channels"]["telegram"]["bot_token"] = None
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app = type(
        "FakeApp",
        (),
        {"core_loader": CoreLoader(), "version_store": FakeVersionStore(target), "runner": FakeRunner()},
    )()

    with pytest.raises(RuntimeError, match="bot_token_env with a value or bot_token"):
        build_enabled_gateway_channels(app)


@pytest.mark.asyncio
async def test_telegram_delivery_calls_send_message():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="hello")],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.sent == [
        {
            "chat_id": "123",
            "text": "hello",
            "reply_to_message_id": None,
            "parse_mode": "MarkdownV2",
            "reply_markup": None,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_delivery_sends_progress_and_notice_as_text():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[
                InteractionDelivery(kind="progress", type="text", text="working"),
                InteractionDelivery(kind="notice", type="text", text="done"),
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert [item["text"] for item in api.sent] == ["working", "done"]
    assert [item["reply_to_message_id"] for item in api.sent] == [None, None]


@pytest.mark.asyncio
async def test_telegram_delivery_sends_media_blocks_in_order():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[
                InteractionDelivery(
                    type="image",
                    text="intro\n\n[artifact:a1 image plot]",
                    fallback_text="intro\n\n[artifact:a1 image plot]",
                    blocks=[
                        {"type": "text", "text": "intro", "metadata": {}},
                        {
                            "type": "image",
                            "text": "plot",
                            "artifact": {
                                "artifact_id": "a1",
                                "kind": "image",
                                "url": "https://example.com/plot.png",
                                "summary": "plot",
                            },
                            "metadata": {},
                        },
                    ],
                    artifacts=[{"artifact_id": "a1", "kind": "image", "url": "https://example.com/plot.png"}],
                )
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.sent[0]["text"] == "intro"
    assert api.media_sent == [
        {
            "kind": "photo",
            "chat_id": "123",
            "value": "https://example.com/plot.png",
            "caption": "plot",
            "reply_to_message_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_delivery_sends_audio_blocks_as_voice(tmp_path, monkeypatch):
    api = FakeApi()
    source = tmp_path / "voice.mp3"
    source.write_bytes(b"MP3DATA")
    converted = {}

    def fake_convert_audio_to_ogg_opus(src, target):
        converted["source"] = src
        converted["target"] = target
        target.write_bytes(b"OGGDATA")

    monkeypatch.setattr("demiurge.channels.telegram.bridge._convert_audio_to_ogg_opus", fake_convert_audio_to_ogg_opus)
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[
                InteractionDelivery(
                    type="audio",
                    text="[artifact:a1 audio voice]",
                    fallback_text="[artifact:a1 audio voice]",
                    blocks=[
                        {
                            "type": "audio",
                            "text": "voice",
                            "artifact": {
                                "artifact_id": "a1",
                                "kind": "audio",
                                "resolved_path": str(source),
                                "summary": "voice",
                            },
                            "metadata": {},
                        }
                    ],
                    artifacts=[{"artifact_id": "a1", "kind": "audio", "resolved_path": str(source)}],
                )
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert converted["source"] == source
    assert converted["target"].suffix == ".ogg"
    assert api.media_sent == []
    assert api.voice_sent == [
        {
            "chat_id": "123",
            "value": str(converted["target"]),
            "caption": "voice",
            "reply_to_message_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_delivery_media_falls_back_to_text():
    api = FakeApi()
    api.fail_media = True
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[
                InteractionDelivery(
                    type="file",
                    text="[artifact:a1 file report]",
                    fallback_text="[artifact:a1 file report]",
                    blocks=[
                        {
                            "type": "file",
                            "artifact": {
                                "artifact_id": "a1",
                                "kind": "file",
                                "resolved_path": "/tmp/report.pdf",
                                "summary": "report",
                            },
                            "metadata": {},
                        }
                    ],
                    artifacts=[{"artifact_id": "a1", "kind": "file", "resolved_path": "/tmp/report.pdf"}],
                )
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.sent[-1]["text"] == r"\[artifact:a1 file report\]"


@pytest.mark.asyncio
async def test_telegram_delivery_audio_conversion_failure_falls_back_to_text(tmp_path, monkeypatch):
    api = FakeApi()
    source = tmp_path / "voice.mp3"
    source.write_bytes(b"MP3DATA")

    def fake_convert_audio_to_ogg_opus(src, target):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr("demiurge.channels.telegram.bridge._convert_audio_to_ogg_opus", fake_convert_audio_to_ogg_opus)
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[
                InteractionDelivery(
                    type="audio",
                    text="[artifact:a1 audio voice]",
                    fallback_text="[artifact:a1 audio voice]",
                    blocks=[
                        {
                            "type": "audio",
                            "artifact": {
                                "artifact_id": "a1",
                                "kind": "audio",
                                "resolved_path": str(source),
                                "summary": "voice",
                            },
                            "metadata": {},
                        }
                    ],
                    artifacts=[{"artifact_id": "a1", "kind": "audio", "resolved_path": str(source)}],
                )
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.voice_sent == []
    assert api.sent[-1]["text"] == r"\[artifact:a1 audio voice\]"


@pytest.mark.asyncio
async def test_telegram_delivery_falls_back_to_plain_on_markdown_parse_error():
    api = FakeApi()
    api.fail_markdown = True
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="**hello**")],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.sent == [
        {
            "chat_id": "123",
            "text": "hello",
            "reply_to_message_id": None,
            "parse_mode": None,
            "reply_markup": None,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_rich_table_sends_raw_markdown_without_legacy_formatting():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")
    table = "## Results\n\n| Fruit | Price |\n| --- | --- |\n| Apple | 5 |"

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=table)],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_sent == [
        {
            "chat_id": "123",
            "markdown": table,
            "reply_to_message_id": None,
        }
    ]
    assert api.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "- [x] done",
        "<details>\n<summary>More</summary>\n\nBody\n</details>",
        "$$x^2$$",
    ],
)
async def test_telegram_rich_only_markdown_constructs_use_rich_path(content):
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=content)],
            metadata={"source": "123", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_sent[0]["markdown"] == content
    assert api.sent == []


@pytest.mark.asyncio
async def test_telegram_plain_format_skips_rich_messages():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        message_format="plain",
    )
    table = "| Fruit | Price |\n| --- | --- |\n| Apple | 5 |"

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=table)],
            metadata={"source": "123", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_attempts == 0
    assert api.sent[0]["parse_mode"] is None
    assert api.sent[0]["text"] == table


@pytest.mark.asyncio
async def test_telegram_rich_endpoint_failure_falls_back_and_latches_off():
    class Endpoint404(RuntimeError):
        code = 404

    api = FakeApi()
    api.fail_rich = Endpoint404("Endpoint 'sendRichMessage' not found")
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")
    table = "| Fruit | Price |\n| --- | --- |\n| Apple | 5 |"

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=table)],
            metadata={"source": "123", "conversation_key": "telegram:123"},
        )
    )
    api.fail_rich = None
    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=table)],
            metadata={"source": "123", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_attempts == 1
    assert len(api.sent) == 2
    assert "Apple" in api.sent[0]["text"]
    assert api.sent[0]["parse_mode"] == "MarkdownV2"


@pytest.mark.asyncio
async def test_telegram_rich_bad_request_falls_back_to_markdownv2():
    api = FakeApi()
    api.fail_rich = RuntimeError("Bad Request: can't parse rich message")
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="| A | B |\n| --- | --- |\n| C | D |")],
            metadata={"source": "123", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_attempts == 1
    assert api.sent[0]["parse_mode"] == "MarkdownV2"
    assert "C" in api.sent[0]["text"]


@pytest.mark.asyncio
async def test_telegram_rich_transient_failure_does_not_legacy_resend():
    api = FakeApi()
    api.fail_rich = RuntimeError("network timed out")
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="| A | B |\n| --- | --- |\n| C | D |")],
            metadata={"source": "123", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_attempts == 1
    assert api.sent == []

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "first", "all"])
async def test_telegram_single_chunk_reply_to_mode_controls_reply_to_message_id(mode):
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        reply_to_mode=mode,
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="hello")],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    expected_reply = 456 if mode != "off" else None
    assert api.sent == [
        {
            "chat_id": "123",
            "text": "hello",
            "reply_to_message_id": expected_reply,
            "parse_mode": "MarkdownV2",
            "reply_markup": None,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_multi_chunk_first_mode_threads_only_first_chunk():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        reply_to_mode="first",
    )
    long_text = "\U0001F600" * 2000 + " " + "\U0001F600" * 2000

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=long_text)],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert len(api.sent) >= 2
    assert api.sent[0]["reply_to_message_id"] == 456
    for sent in api.sent[1:]:
        assert sent["reply_to_message_id"] is None


@pytest.mark.asyncio
async def test_telegram_multi_chunk_all_mode_threads_every_chunk():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        reply_to_mode="all",
    )
    long_text = "\U0001F600" * 2000 + " " + "\U0001F600" * 2000

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=long_text)],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert len(api.sent) >= 2
    assert all(sent["reply_to_message_id"] == 456 for sent in api.sent)


@pytest.mark.asyncio
async def test_telegram_multi_chunk_off_mode_threads_no_chunk():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        reply_to_mode="off",
    )
    long_text = "\U0001F600" * 2000 + " " + "\U0001F600" * 2000

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=long_text)],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert len(api.sent) >= 2
    assert all(sent["reply_to_message_id"] is None for sent in api.sent)


@pytest.mark.asyncio
async def test_telegram_rich_message_off_mode_omits_reply_parameters():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        reply_to_mode="off",
    )
    table = "| Fruit | Price |\n| --- | --- |\n| Apple | 5 |"

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=table)],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_sent == [
        {
            "chat_id": "123",
            "markdown": table,
            "reply_to_message_id": None,
        }
    ]
    assert api.sent == []


@pytest.mark.asyncio
async def test_telegram_rich_message_first_mode_includes_reply_parameters():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        reply_to_mode="first",
    )
    table = "| Fruit | Price |\n| --- | --- |\n| Apple | 5 |"

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text=table)],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.rich_sent == [
        {
            "chat_id": "123",
            "markdown": table,
            "reply_to_message_id": 456,
        }
    ]
    assert api.sent == []


def test_telegram_default_reply_to_mode_is_off():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")
    assert bridge.reply_to_mode == "off"


def test_telegram_bridge_from_config_propagates_reply_to_mode():
    config = TelegramChannelConfig(bot_token="x", reply_to_mode="off")
    bridge = TelegramInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)
    assert bridge.reply_to_mode == "off"


def test_telegram_channel_config_reply_to_mode_default_is_off():
    config = TelegramChannelConfig(bot_token="x")
    assert config.reply_to_mode == "off"


def test_telegram_channel_config_access_policy_validation():
    config = TelegramChannelConfig(bot_token="x", allowed_users=[42], allowed_chats=[-100])

    assert config.allowed_users == [42]
    assert config.allowed_chats == [-100]
    assert config.unauthorized_response == "brief"

    with pytest.raises(ValidationError):
        TelegramChannelConfig(bot_token="x", allowed_users=["42"])
    with pytest.raises(ValidationError):
        TelegramChannelConfig(bot_token="x", allowed_chats=["-100"])
    with pytest.raises(ValidationError):
        TelegramChannelConfig(bot_token="x", unauthorized_response="loud")


def test_should_thread_reply_helper():
    assert _should_thread_reply(None, 0, "first") is False
    assert _should_thread_reply(7, 0, "first") is True
    assert _should_thread_reply(7, 1, "first") is False
    assert _should_thread_reply(7, 0, "all") is True
    assert _should_thread_reply(7, 1, "all") is True
    assert _should_thread_reply(7, 0, "off") is False
    assert _should_thread_reply(7, 1, "off") is False


@pytest.mark.asyncio
async def test_telegram_prompt_sends_choices_and_maps_next_number():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            prompt=UserPromptRequest(
                question="Which path?",
                choices=["fast", "careful"],
                conversation_key="telegram:123",
                metadata={"source": "123", "reply_to": "456"},
            ),
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )
    answer = bridge.normalize_update(_message("2", chat_id=123, message_id=457))

    assert api.sent == [
        {
            "chat_id": "123",
            "text": "Which path?\n1\\. fast\n2\\. careful",
            "reply_to_message_id": None,
            "parse_mode": "MarkdownV2",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "1. fast", "callback_data": "choice:0"}],
                    [{"text": "2. careful", "callback_data": "choice:1"}],
                ]
            },
        }
    ]
    assert answer.text == "careful"


@pytest.mark.asyncio
async def test_telegram_prompt_choice_callback_maps_to_next_answer():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            prompt=UserPromptRequest(
                question="Which path?",
                choices=["fast", "careful"],
                conversation_key="telegram:123",
                metadata={"source": "123", "reply_to": "456"},
            ),
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    await bridge.handle_update(_callback("choice:1", chat_id=123, message_id=456, callback_id="cb_2"))
    state = bridge._conversations["telegram:123"]
    assert api.callbacks == [{"callback_query_id": "cb_2", "text": None}]
    await state.active_task
    assert state.runtime.runner.texts == ["careful"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("allow", "allow"),
        ("session", "always_allow_for_session"),
        ("deny", "deny"),
    ],
)
async def test_telegram_private_approval_callback_returns_decision(action, expected):
    runner = ApprovalRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="run",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
            metadata={"telegram_chat_type": "private"},
        )
    )
    await asyncio.wait_for(runner.request_started.wait(), timeout=1)
    await _wait_until(lambda: bool(api.sent))
    await _wait_until(lambda: bool(bridge._pending_approvals))
    approval_id = next(iter(bridge._pending_approvals))
    state = bridge._conversations["telegram:123"]
    task = state.active_task

    assert task is not None and not task.done()
    assert "Approval required" in api.sent[0]["text"]
    assert "whoami" in api.sent[0]["text"]
    assert api.sent[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Allow once", "callback_data": f"approval:{approval_id}:allow"}],
            [{"text": "Allow for session", "callback_data": f"approval:{approval_id}:session"}],
            [{"text": "Deny", "callback_data": f"approval:{approval_id}:deny"}],
        ]
    }

    await bridge.handle_update(_callback(f"approval:{approval_id}:{action}", chat_id=123, message_id=456, callback_id="cb_approval"))
    await asyncio.wait_for(task, timeout=1)

    assert runner.decision.value == expected
    assert bridge._pending_approvals == {}
    assert api.callbacks == [
        {"callback_query_id": "cb_approval", "text": "Approved." if expected != "deny" else "Denied."}
    ]
    assert api.edits
    edited = api.edits[-1]
    assert edited["message_id"] == 1000
    assert edited["reply_markup"] is None
    assert ("Denied" if expected == "deny" else "Approved") in edited["text"]
    assert api.reply_markup_edits[-1] == {"chat_id": "123", "message_id": 1000, "reply_markup": None}


@pytest.mark.asyncio
async def test_telegram_group_approval_fails_closed():
    runner = ApprovalRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(runner), api=api, bot_username="demiurge_bot")

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="run",
            source="-123",
            reply_to="456",
            conversation_key="telegram:-123",
            metadata={"telegram_chat_type": "group"},
        )
    )
    state = bridge._conversations["telegram:-123"]
    await state.active_task

    assert runner.decision.value == "deny"
    assert "only supported in private chat" in runner.decision.reason
    assert any("only supported in private chat" in item["text"] for item in api.sent)
    assert bridge._pending_approvals == {}


@pytest.mark.asyncio
async def test_telegram_approval_times_out_and_denies():
    runner = ApprovalRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        approval_timeout_seconds=0.01,
    )

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="run",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
            metadata={"telegram_chat_type": "private"},
        )
    )
    state = bridge._conversations["telegram:123"]
    await state.active_task

    assert runner.decision.value == "deny"
    assert "timed out" in runner.decision.reason
    assert bridge._pending_approvals == {}
    assert api.edits
    assert "Approval expired" in api.edits[-1]["text"]
    assert api.edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_telegram_stop_cancels_pending_approval_and_expires_callback():
    runner = ApprovalRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="run",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
            metadata={"telegram_chat_type": "private"},
        )
    )
    await asyncio.wait_for(runner.request_started.wait(), timeout=1)
    await _wait_until(lambda: bool(bridge._pending_approvals))
    task = bridge._conversations["telegram:123"].active_task
    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="/stop",
            source="123",
            reply_to="457",
            conversation_key="telegram:123",
            metadata={"telegram_chat_type": "private"},
        )
    )

    if task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert bridge._pending_approvals == {}
    assert api.edits
    assert "Approval expired" in api.edits[-1]["text"]
    await bridge.handle_update(_callback("approval:1:allow", chat_id=123, message_id=456, callback_id="cb_old"))
    assert {"callback_query_id": "cb_old", "text": "Approval expired."} in api.callbacks
    assert "Approval expired" in api.edits[-1]["text"]


@pytest.mark.asyncio
async def test_telegram_terminal_does_not_execute_before_approval_and_deny_blocks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = tmp_path / "terminal-script.json"
    write_json(
        script,
        [
            {
                "tool_calls": [
                    {
                        "id": "terminal_1",
                        "name": "terminal",
                        "arguments": {"command": "printf ran > out.txt"},
                    }
                ]
            },
            {"content": "done"},
        ],
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", fake_script=script, workspace=workspace)
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(app.runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
    )

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="run terminal",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
            metadata={"telegram_chat_type": "private"},
        )
    )
    await _wait_until(lambda: bool(bridge._pending_approvals), timeout=20)

    assert not (workspace / "out.txt").exists()

    state = bridge._conversations["telegram:123"]
    task = state.active_task
    await bridge.handle_update(_callback("approval:1:deny", chat_id=123, message_id=456, callback_id="cb_deny"))
    if task is not None:
        await task

    assert not (workspace / "out.txt").exists()
    events = app.runner.event_log.tail(50)
    assert any(event["type"] == "approval.denied" for event in events)


@pytest.mark.asyncio
async def test_telegram_tool_display_quiet_suppresses_tool_results():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        tool_display="quiet",
    )
    record = ToolExecutionRecord(
        call=ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1"),
        result=ToolResult(content="alice\n", display_output="alice\n"),
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="done")],
            tool_result_list=[record],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert [item["text"] for item in api.sent] == ["done"]


@pytest.mark.asyncio
async def test_telegram_tool_display_summary_sends_before_assistant_delivery():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")
    record = ToolExecutionRecord(
        call=ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1"),
        result=ToolResult(content="alice\n", display_output="alice\n"),
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="done")],
            tool_result_list=[record],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert "Tool calls" in api.sent[0]["text"]
    assert "terminal" in api.sent[0]["text"]
    assert "alice" in api.sent[0]["text"]
    assert api.sent[1]["text"] == "done"


@pytest.mark.asyncio
async def test_telegram_tool_lifecycle_edits_started_message():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")
    call = ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1")
    result = ToolResult(content="exit_code: 0\nstdout:\nalice\n", display_output="$ whoami\ncwd: .\nexit_code: 0\nstdout:\nalice\n")

    await bridge.deliver(
        _outbound(
            channel="telegram",
            items=[
                InteractionItem.tool_call_item(ToolInteractionRecord.started(call)),
                InteractionItem.tool_call_item(ToolInteractionRecord.finished(ToolExecutionRecord(call=call, result=result))),
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert len(api.sent) == 1
    assert "running" in api.sent[0]["text"]
    assert len(api.edits) == 1
    assert api.edits[0]["message_id"] == 1000
    assert "alice" in api.edits[0]["text"]


@pytest.mark.asyncio
async def test_telegram_delivery_before_tool_result_preserves_item_order():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")
    record = ToolExecutionRecord(
        call=ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1"),
        result=ToolResult(content="alice\n", display_output="alice\n"),
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            items=[
                InteractionItem.delivery_item(InteractionDelivery(type="text", text="done")),
                InteractionItem.tool_result_item(record),
            ],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert api.sent[0]["text"] == "done"
    assert "Tool calls" in api.sent[1]["text"]
    assert "terminal" in api.sent[1]["text"]


@pytest.mark.asyncio
async def test_telegram_tool_display_full_includes_arguments_and_result():
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(FakeRunner()),
        api=api,
        bot_username="demiurge_bot",
        tool_display="full",
    )
    record = ToolExecutionRecord(
        call=ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1"),
        result=ToolResult(content="alice\n", display_output="alice\n", model_output="model sees alice"),
    )

    await bridge.deliver(
        _outbound(
            channel="telegram",
            delivery_list=[InteractionDelivery(type="text", text="done")],
            tool_result_list=[record],
            metadata={"source": "123", "reply_to": "456", "conversation_key": "telegram:123"},
        )
    )

    assert "Arguments" in api.sent[0]["text"]
    assert '"command": "whoami"' in api.sent[0]["text"]
    assert "Result" in api.sent[0]["text"]
    assert "model sees alice" in api.sent[0]["text"]
    assert api.sent[1]["text"] == "done"


@pytest.mark.asyncio
async def test_telegram_registers_surface_commands():
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(FakeRunner()), api=api, bot_username="demiurge_bot")

    await bridge.register_commands()

    registered = {item["command"] for item in api.commands[0]}
    assert {"help", "status", "new", "stop", "queue", "busy", "sessions", "resume", "tools", "skills", "skill"} <= registered
    assert "tool-display" not in registered


@pytest.mark.asyncio
async def test_telegram_status_slash_bypasses_model_turn():
    runner = FakeRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        allowed_users=[42],
        allowed_chats=[-100],
    )

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="/status",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
            metadata={"telegram_chat_id": 123, "telegram_user_id": 42, "telegram_chat_type": "private"},
        )
    )

    assert runner.texts == []
    assert "Status" in api.sent[0]["text"]
    assert "access: `restricted`" in api.sent[0]["text"]
    assert "allowed users: `1`" in api.sent[0]["text"]
    assert "allowed chats: `1`" in api.sent[0]["text"]
    assert "current authorized: `true`" in api.sent[0]["text"]


@pytest.mark.asyncio
async def test_telegram_queue_command_runs_prompt_not_command():
    runner = FakeRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(runner), api=api, bot_username="demiurge_bot")

    await bridge.handle_inbound(
        InteractionInbound(
            channel="telegram",
            text="/queue queued prompt",
            source="123",
            reply_to="456",
            conversation_key="telegram:123",
        )
    )
    state = bridge._conversations["telegram:123"]
    await state.active_task

    assert runner.texts == ["queued prompt"]
    assert any("Queued:" in sent["text"] for sent in api.sent)


@pytest.mark.asyncio
async def test_telegram_queue_busy_mode_runs_followup_after_current_turn():
    runner = BlockingRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        busy_mode="queue",
    )

    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="first", source="123", reply_to="1", conversation_key="telegram:123")
    )
    await asyncio.wait_for(runner.first_started.wait(), timeout=1)
    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="second", source="123", reply_to="2", conversation_key="telegram:123")
    )

    assert runner.texts == ["first"]
    assert any("Queued for next turn" in sent["text"] for sent in api.sent)
    runner.release.set()
    await asyncio.wait_for(runner.second_started.wait(), timeout=1)
    state = bridge._conversations["telegram:123"]
    if state.active_task is not None:
        await state.active_task
    assert runner.texts == ["first", "second"]


@pytest.mark.asyncio
async def test_telegram_interrupt_busy_mode_cancels_current_turn_and_runs_latest():
    runner = BlockingRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(runtime=InteractionRuntime(runner), api=api, bot_username="demiurge_bot")

    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="first", source="123", reply_to="1", conversation_key="telegram:123")
    )
    await asyncio.wait_for(runner.first_started.wait(), timeout=1)
    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="second", source="123", reply_to="2", conversation_key="telegram:123")
    )

    await asyncio.wait_for(runner.second_started.wait(), timeout=1)
    state = bridge._conversations["telegram:123"]
    if state.active_task is not None:
        await state.active_task
    assert runner.cancelled is True
    assert runner.texts == ["first", "second"]


@pytest.mark.asyncio
async def test_telegram_stop_command_cancels_active_turn_and_clears_queue():
    runner = BlockingRunner()
    api = FakeApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(runner),
        api=api,
        bot_username="demiurge_bot",
        busy_mode="queue",
    )

    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="first", source="123", reply_to="1", conversation_key="telegram:123")
    )
    await asyncio.wait_for(runner.first_started.wait(), timeout=1)
    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="second", source="123", reply_to="2", conversation_key="telegram:123")
    )
    await bridge.handle_inbound(
        InteractionInbound(channel="telegram", text="/stop", source="123", reply_to="3", conversation_key="telegram:123")
    )

    state = bridge._conversations["telegram:123"]
    assert runner.cancelled is True
    assert state.queue.empty()
    assert any("Stopped current turn" in sent["text"] for sent in api.sent)


def test_cli_gateway_subcommand_runs_gateway(monkeypatch, tmp_path):
    calls = {}
    fake_app = object()

    def fake_create_app(**kwargs):
        calls["create_app"] = kwargs
        return fake_app

    def fake_run_gateway(app):
        calls["gateway_app"] = app

    def fake_run_tui(args):
        raise AssertionError("TUI should not run for gateway subcommand")

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli, "run_gateway", fake_run_gateway)
    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["gateway", "--home", str(tmp_path / "home"), "--core", "assistant"])

    assert calls["create_app"]["core_id"] == "assistant"
    assert calls["gateway_app"] is fake_app


def test_cli_default_runs_tui(monkeypatch, tmp_path):
    calls = {}

    def fake_create_app(**kwargs):
        raise AssertionError("default TUI launcher should create the app in the gateway process")

    def fake_run_gateway(app):
        raise AssertionError("gateway should not run without gateway subcommand")

    def fake_run_tui(args):
        calls["tui_core"] = args.core

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli, "run_gateway", fake_run_gateway)
    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(tmp_path / "home"), "--core", "assistant"])

    assert calls["tui_core"] == "assistant"


def test_cli_legacy_channel_subcommand_is_removed():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["channel", "telegram"])
