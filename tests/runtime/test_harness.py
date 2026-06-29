import pytest
import yaml

from demiurge.app import create_app
from demiurge.security.approval import StaticApprovalProvider
from demiurge.runtime.interactions import InteractionInbound
from demiurge.providers import LLMResponse, ToolCall
from demiurge.util import write_json


def _set_capabilities(app, *, capabilities: dict[str, dict]):
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    defaults = raw.setdefault("capabilities", {}).setdefault("defaults", {})
    defaults.update(capabilities)
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _delivery_texts(result) -> list[str]:
    return [delivery.text for delivery in result.deliveries]


def _install_project_notes_skill(app):
    root = app.version_store.active_core_path("assistant") / "agent" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-notes.md").write_text(
        "---\n"
        "name: project-notes\n"
        "description: Summarize project context.\n"
        "category: development\n"
        "---\n\n"
        "# Project Notes\n\n"
        "Use this skill when project context matters.\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_fake_provider_turn_executes_tool_and_output_delivery(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    result = await app.runner.run_turn("please use tools_list")

    assert result.deliveries
    assert result.tool_results[0].call.name == "tools_list"
    assert _delivery_texts(result)[0].startswith("[fake] tool result received")
    events = [event["type"] for event in app.runner.event_log.tail(20)]
    assert "actions.requested" in events
    assert "action.result" in events
    assert "turn.completed" in events


@pytest.mark.asyncio
async def test_fake_provider_read_file_tool_result_reaches_next_step(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("workspace note", encoding="utf-8")
    script = tmp_path / "read-script.json"
    write_json(
        script,
        [
            {"tool_calls": [{"id": "read_1", "name": "read_file", "arguments": {"path": "note.txt"}}]},
            {"content": "read complete"},
        ],
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", fake_script=script, workspace=workspace)

    result = await app.runner.run_turn("read note")

    assert result.tool_results[0].call.name == "read_file"
    assert "workspace note" in result.tool_results[0].result.content
    assert _delivery_texts(result)[0] == "read complete"
    events = [event["type"] for event in app.runner.event_log.tail(20)]
    assert "approval.decided" in events
    assert "action.result" in events


@pytest.mark.asyncio
async def test_fake_provider_write_file_approval_allow_and_deny_paths(tmp_path):
    allow_workspace = tmp_path / "allow-workspace"
    deny_workspace = tmp_path / "deny-workspace"
    allow_workspace.mkdir()
    deny_workspace.mkdir()
    allow_script = tmp_path / "write-allow.json"
    deny_script = tmp_path / "write-deny.json"
    script_body = [
        {"tool_calls": [{"id": "write_1", "name": "write_file", "arguments": {"path": "out.txt", "content": "ok"}}]},
        {"content": "write complete"},
    ]
    write_json(allow_script, script_body)
    write_json(deny_script, script_body)

    allowed_app = create_app(
        home=tmp_path / "allow-home",
        provider_name="fake",
        fake_script=allow_script,
        workspace=allow_workspace,
    )
    _set_capabilities(allowed_app, capabilities={"fs.write": {"scope": "workspace"}})
    allowed_app.approval_runtime.provider = StaticApprovalProvider("allow")
    allowed = await allowed_app.runner.run_turn("write")

    denied_app = create_app(
        home=tmp_path / "deny-home",
        provider_name="fake",
        fake_script=deny_script,
        workspace=deny_workspace,
    )
    _set_capabilities(denied_app, capabilities={"fs.write": {"scope": "workspace"}})
    denied_app.approval_runtime.provider = StaticApprovalProvider("deny")
    denied = await denied_app.runner.run_turn("write")

    assert allowed.tool_results[0].result.is_error is False
    assert (allow_workspace / "out.txt").read_text(encoding="utf-8") == "ok"
    assert denied.tool_results[0].result.is_error is True
    assert denied.tool_results[0].result.data["executionStarted"] is False
    assert not (deny_workspace / "out.txt").exists()
    events = [event["type"] for event in denied_app.runner.event_log.tail(20)]
    assert "approval.denied" in events


@pytest.mark.asyncio
async def test_skill_index_and_skill_view_progressive_loading(tmp_path):
    class InspectingProvider:
        def __init__(self):
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="skill_1",
                            name="skill_view",
                            arguments={"name": "project-notes"},
                        )
                    ]
                )
            tool_text = next(message.content for message in request.messages if message.role == "tool")
            return LLMResponse(content=f"loaded: {'Project Notes' in tool_text}")

    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_project_notes_skill(app)
    provider = InspectingProvider()
    app.runner.provider = provider

    result = await app.runner.run_turn("load a skill")

    first_request = provider.requests[0]
    skill_index = next(
        message.content
        for message in first_request.messages
        if message.role == "system" and "## Skills (progressive loading)" in message.content
    )
    assert "skill_view(name)" in skill_index
    assert "MUST call skill_view(name)" in skill_index
    assert "- project-notes [development]: Summarize project context" in skill_index
    assert "Use this skill when" not in skill_index
    assert "Start from the exact failing command" not in skill_index
    assert all("Project Notes" not in message.content for message in first_request.messages)
    assert result.tool_results[0].call.name == "skill_view"
    assert "Project Notes" in result.tool_results[0].result.model_output
    assert _delivery_texts(result)[0] == "loaded: True"
    assert any(message.role == "tool" and "Project Notes" in message.content for message in app.runner.history)
    action_event = next(event for event in app.runner.event_log.tail(20) if event["type"] == "action.result")
    assert "Project Notes" in action_event["model_output"]
    assert action_event["data"]["skill_id"] == "project-notes"


@pytest.mark.asyncio
async def test_clarify_terminates_turn_with_needs_user(tmp_path):
    script = tmp_path / "ask-script.json"
    write_json(
        script,
        [{"tool_calls": [{"id": "ask_1", "name": "clarify", "arguments": {"question": "Which path?"}}]}],
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", fake_script=script)

    result = await app.runner.run_turn("ask me")

    assert result.needs_user is True
    assert _delivery_texts(result)[0] == "Which path?"
    assert result.tool_results[0].result.data["needs_user"] is True


@pytest.mark.asyncio
async def test_interaction_metadata_is_written_to_events(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    await app.runner.run_turn(
        "hello",
        interaction=InteractionInbound(
            channel="telegram",
            text="hello",
            source="chat-1",
            reply_to="message-1",
            conversation_key="telegram:chat-1",
        ),
    )

    events = app.runner.event_log.tail(30)
    received = next(event for event in events if event["type"] == "message.received")
    assert received["channel"] == "telegram"
    assert received["source"] == "chat-1"
    assert received["reply_to"] == "message-1"
    assert received["conversation_key"] == "telegram:chat-1"


@pytest.mark.asyncio
async def test_approval_events_include_channel_metadata(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    script = tmp_path / "read-script.json"
    write_json(
        script,
        [
            {"tool_calls": [{"id": "read_1", "name": "read_file", "arguments": {"path": "note.txt"}}]},
            {"content": "done"},
        ],
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", fake_script=script, workspace=workspace)

    await app.runner.run_turn(
        "read",
        interaction=InteractionInbound(
            channel="telegram",
            text="read",
            source="chat-1",
            reply_to="message-1",
            conversation_key="telegram:chat-1",
        ),
    )

    approval = next(event for event in app.runner.event_log.tail(20) if event["type"] == "approval.decided")
    assert approval["channel"] == "telegram"
    assert approval["source"] == "chat-1"
    assert approval["conversation_key"] == "telegram:chat-1"
