import pytest

from demiurge.app import create_app
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime
from demiurge.providers import LLMResponse, ToolCall


class EchoInspectingProvider:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if request.metadata.get("kind") == "session_compaction":
            return LLMResponse(content="Summary of compacted historical turns.")
        user_text = next((message.content for message in reversed(request.messages) if message.role == "user"), "")
        return LLMResponse(content=f"assistant: {user_text}")


@pytest.mark.asyncio
async def test_session_messages_persist_and_resume_across_app_restart(tmp_path):
    home = tmp_path / "home"
    first = create_app(home=home, provider_name="fake")
    provider = EchoInspectingProvider()
    first.runner.provider = provider

    result = await first.runner.run_turn("first message")
    session_id = result.session_id

    second = create_app(home=home, provider_name="fake", session_id=session_id)
    second_provider = EchoInspectingProvider()
    second.runner.provider = second_provider
    await second.runner.run_turn("second message")

    persisted = second.runner.session_store.read_messages(session_id)
    assert [message.role for message in persisted if message.kind == "message"] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert any(message.content == "first message" for message in second_provider.requests[0].messages)
    assert any(message.content == "assistant: first message" for message in second_provider.requests[0].messages)


@pytest.mark.asyncio
async def test_session_json_files_preserve_utf8_text(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = EchoInspectingProvider()

    result = await app.runner.run_turn("中文消息")

    messages_raw = app.runner.session_store.messages_path(result.session_id).read_text(encoding="utf-8")
    session_raw = app.runner.session_store.session_path(result.session_id).read_text(encoding="utf-8")
    events_raw = app.runner.event_log.path.read_text(encoding="utf-8")
    assert "中文消息" in messages_raw
    assert "中文消息" in session_raw
    assert "中文消息" in events_raw
    assert "\\u4e2d\\u6587\\u6d88\\u606f" not in messages_raw


@pytest.mark.asyncio
async def test_context_assembler_emits_default_assistant_layers(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = EchoInspectingProvider()
    app.runner.provider = provider

    await app.runner.run_turn("hello")

    request = provider.requests[0]
    layer_event = next(event for event in app.runner.event_log.tail(20) if event["type"] == "context.assembled")
    assert [layer["name"] for layer in layer_event["layers"]] == [
        "core_soul",
        "current_turn",
    ]
    assert all("## Skills (progressive loading)" not in message.content for message in request.messages)
    assert all("Project Notes" not in message.content for message in request.messages)


@pytest.mark.asyncio
async def test_conversation_key_routes_to_durable_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = EchoInspectingProvider()
    runtime = InteractionRuntime(app.runner)

    first = await runtime.handle(
        InteractionInbound(channel="telegram", text="chat one", source="1", conversation_key="telegram:1")
    )
    second = await runtime.handle(
        InteractionInbound(channel="telegram", text="chat two", source="2", conversation_key="telegram:2")
    )
    third = await runtime.handle(
        InteractionInbound(channel="telegram", text="chat one again", source="1", conversation_key="telegram:1")
    )

    assert first.session_id != second.session_id
    assert third.session_id == first.session_id
    assert app.runner.session_store.get(first.session_id).conversation_key == "telegram:1"
    assert app.runner.session_store.get(second.session_id).conversation_key == "telegram:2"


@pytest.mark.asyncio
async def test_manual_compaction_keeps_summary_and_excludes_compacted_history_from_context(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = EchoInspectingProvider()
    app.runner.provider = provider
    for index in range(8):
        await app.runner.run_turn(f"user {index}")

    result = await app.runner.compact_session(focus="preserve decisions")

    assert result.error is None
    assert result.summary_message_id is not None
    assert result.compacted_count > 0
    assert "REFERENCE ONLY" in result.summary

    await app.runner.run_turn("after compact")
    request = provider.requests[-1]
    joined = "\n".join(message.content for message in request.messages)
    assert "Summary of compacted historical turns." in joined
    assert "user 0" not in joined
    assert "user 7" in joined
    assert "after compact" in joined


@pytest.mark.asyncio
async def test_compaction_failure_does_not_change_session_history(tmp_path):
    class FailingCompactProvider(EchoInspectingProvider):
        async def complete(self, request):
            if request.metadata.get("kind") == "session_compaction":
                raise RuntimeError("summary failed")
            return await super().complete(request)

    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = FailingCompactProvider()
    for index in range(4):
        await app.runner.run_turn(f"user {index}")
    before = app.runner.session_store.message_count(app.runner.session_id)

    result = await app.runner.compact_session(protect_last_n=2)

    assert result.error == "summary failed"
    assert app.runner.session_store.message_count(app.runner.session_id) == before
    assert app.runner.session_store.latest_compaction_summary(app.runner.session_id) is None


@pytest.mark.asyncio
async def test_compaction_uses_complete_turn_boundaries_for_tool_transcript(tmp_path):
    class ToolThenEchoProvider(EchoInspectingProvider):
        def __init__(self):
            super().__init__()
            self.tool_requested = False

        async def complete(self, request):
            self.requests.append(request)
            if request.metadata.get("kind") == "session_compaction":
                return LLMResponse(content="Summary with complete tool transcript.")
            user_text = next((message.content for message in reversed(request.messages) if message.role == "user"), "")
            has_tool_result = any(message.role == "tool" for message in request.messages)
            if user_text == "use tool" and not self.tool_requested and not has_tool_result:
                self.tool_requested = True
                return LLMResponse(tool_calls=[ToolCall(id="tools_1", name="tools_list", arguments={})])
            return LLMResponse(content=f"assistant: {user_text}")

    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = ToolThenEchoProvider()
    app.runner.provider = provider
    first = await app.runner.run_turn("use tool")
    await app.runner.run_turn("plain one")
    await app.runner.run_turn("plain two")

    first_turn_messages = [
        message
        for message in app.runner.session_store.read_messages(app.runner.session_id)
        if message.turn_id == first.turn_id
    ]
    assert [message.role for message in first_turn_messages] == ["user", "assistant", "tool", "assistant"]

    result = await app.runner.compact_session(protect_last_n=2)

    assert result.error is None
    marker_id = app.runner.session_store.get(app.runner.session_id).compacted_until_message_id
    marker = next(message for message in first_turn_messages if message.id == marker_id)
    assert marker.role == "assistant"
    assert marker.content == "assistant: use tool"
    compaction_request = next(request for request in provider.requests if request.metadata.get("kind") == "session_compaction")
    transcript = compaction_request.messages[-1].content
    assert "TOOL_CALLS" in transcript
    assert "TOOL tools_list" in transcript
    assert "assistant: use tool" in transcript
