import importlib
import json
import sys
import types
from types import SimpleNamespace

import pytest
import yaml

from demiurge.app import create_app
from demiurge.packages import (
    PackageManager,
    PackageRepository,
    default_package_repository_root,
    load_package_repository_collection,
)
from demiurge.providers import LLMResponse, ToolCall


HONCHO_TOOLS = {
    "honcho_profile",
    "honcho_search",
    "honcho_context",
    "honcho_reasoning",
    "honcho_conclude",
}


def _manager(app) -> PackageManager:
    repositories = load_package_repository_collection(
        home=app.home,
        repository_configs={"builtin": {"type": "builtin"}},
    )
    return PackageManager(version_store=app.version_store, repository=repositories)


class _RecordingProvider:
    def __init__(self, responses=None, *, default: str = "main"):
        self.responses = list(responses or [])
        self.default = default
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, LLMResponse):
                return item
            return LLMResponse(content=str(item))
        return LLMResponse(content=self.default)


class _FakeHonchoState:
    def __init__(self) -> None:
        self.instances = []
        self.sessions = {}
        self.peers = {}
        self.messages = []
        self.deleted = []
        self.cards = {
            "allen": ["User prefers Chinese replies.", "User works in demiurge-agent."],
            "demiurge-assistant": ["Assistant should use concise engineering prose."],
        }


class _FakePeer:
    def __init__(self, state: _FakeHonchoState, peer_id: str) -> None:
        self.state = state
        self.id = peer_id

    def message(self, content: str):
        return {"peer": self.id, "content": content}

    def context(self, **kwargs):
        search_query = str(kwargs.get("search_query") or "").strip()
        if self.id == "demiurge-assistant":
            representation = "AI representation for Demiurge assistant."
        elif search_query:
            representation = f"User representation for query: {search_query}"
        else:
            representation = "User representation from Honcho."
        return SimpleNamespace(
            representation=representation,
            peer_card=self.state.cards.get(self.id, [f"Card for {self.id}"]),
        )

    def get_card(self):
        return list(self.state.cards.get(self.id, []))

    def set_card(self, card):
        self.state.cards[self.id] = list(card)
        return list(card)

    def chat(self, query: str, **kwargs):
        level = kwargs.get("reasoning_level") or "default"
        return f"Reasoned ({level}): {query}"

    def create_conclusion(self, conclusion: str):
        self.state.cards.setdefault(self.id, []).append(conclusion)
        return {"id": f"conclusion-{len(self.state.cards[self.id])}"}

    def delete_conclusion(self, delete_id: str):
        self.state.deleted.append((self.id, delete_id))
        return True


class _FakeSession:
    def __init__(self, state: _FakeHonchoState, session_id: str) -> None:
        self.state = state
        self.id = session_id
        self.peers = []

    def add_peers(self, peers):
        self.peers.extend(getattr(peer, "id", str(peer)) for peer in peers)

    def add_messages(self, messages):
        self.state.messages.extend(messages)

    def context(self, **kwargs):
        return SimpleNamespace(summary=SimpleNamespace(content=f"Summary for {self.id}"))


def _install_fake_honcho(monkeypatch) -> _FakeHonchoState:
    state = _FakeHonchoState()

    class FakeHoncho:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.state = state
            state.instances.append(self)

        def session(self, session_id: str):
            return state.sessions.setdefault(session_id, _FakeSession(state, session_id))

        def peer(self, peer_id: str):
            return state.peers.setdefault(peer_id, _FakePeer(state, peer_id))

    module = types.ModuleType("honcho")
    module.Honcho = FakeHoncho
    monkeypatch.setitem(sys.modules, "honcho", module)
    return state


def _request_text(provider: _RecordingProvider, index: int = 0) -> str:
    return "\n".join(message.content for message in provider.requests[index].messages)


def _delivery_text(result) -> str:
    return "\n".join(delivery.text for delivery in result.deliveries if delivery.text)


def test_builtin_repository_lists_memory_honcho_package():
    repository = PackageRepository.load(default_package_repository_root())

    package = repository.packages["memory_honcho"]
    assert {"memory", "context", "provider:honcho"}.issubset(package.tags)
    assert package.manual_dependencies == ["honcho-ai"]
    assert [option.option_id for option in package.options] == [
        "recall_mode",
        "enable_tools",
        "api_key",
        "base_url",
        "workspace",
        "peer_name",
        "ai_peer",
        "session_strategy",
        "context_tokens",
        "timeout_seconds",
        "context_cadence",
    ]
    assert package.options[0].choices == ["hybrid", "context", "tools"]
    assert package.options[0].default == "hybrid"
    assert package.options[1].default is True
    assert [component.component_id for component in package.components] == [
        "memory_honcho_lib",
        "memory_honcho_bootstrap",
        "memory_honcho_recall",
        "memory_honcho_sync",
        "honcho_profile_tool",
        "honcho_search_tool",
        "honcho_context_tool",
        "honcho_reasoning_tool",
        "honcho_conclude_tool",
        "memory_honcho_skill",
    ]
    assert package.components[1].pipeline == {"after": "session_context"}
    assert package.components[2].pipeline == {"group": "serial", "before": "base_input"}
    assert package.components[3].pipeline == {"group": "parallel"}
    assert all(component.when == {"enable_tools": True} for component in package.components[4:9])


def test_install_and_uninstall_memory_honcho_preserves_data(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    result = manager.install(
        core_id="assistant",
        package_id="memory_honcho",
        option_answers={"api_key": "test-key", "peer_name": "allen"},
    )

    core_path = app.version_store.active_core_path("assistant")
    assert result.registry_path == core_path / "packages.yaml"
    assert result.warnings == ["manual dependency review required: honcho-ai"]
    assert (core_path / "agent" / "lib" / "memory_honcho" / "runtime.py").exists()
    assert (core_path / "agent" / "bootstrap" / "memory_honcho" / "module.py").exists()
    assert (core_path / "agent" / "input" / "memory_honcho_recall" / "module.py").exists()
    assert (core_path / "agent" / "output" / "memory_honcho_sync" / "module.py").exists()
    assert (core_path / "agent" / "skills" / "memory_honcho" / "SKILL.md").exists()
    assert (core_path / "agent" / "bootstrap" / "memory_honcho" / "config.yaml").exists()
    assert (core_path / "agent" / "input" / "memory_honcho_recall" / "config.yaml").exists()
    assert (core_path / "agent" / "output" / "memory_honcho_sync" / "config.yaml").exists()
    for tool in HONCHO_TOOLS:
        assert (core_path / "agent" / "tools" / tool / "module.py").exists()

    config = yaml.safe_load((core_path / "agent" / "lib" / "memory_honcho" / "config.yaml").read_text())
    assert config["recall_mode"] == "hybrid"
    assert config["api_key"] == "test-key"
    assert config["peer_name"] == "allen"
    assert config["storage"] == {"relative_to": "core_root", "path": "memory/honcho"}
    registry = yaml.safe_load((core_path / "packages.yaml").read_text())
    assert registry["installed"][0]["options"]["api_key"] == "<redacted>"

    bootstrap_pipeline = yaml.safe_load((core_path / "agent" / "bootstrap" / "pipeline.yaml").read_text())
    input_pipeline = yaml.safe_load((core_path / "agent" / "input" / "pipeline.yaml").read_text())
    output_pipeline = yaml.safe_load((core_path / "agent" / "output" / "pipeline.yaml").read_text())
    assert bootstrap_pipeline["serial"] == ["session_context", "memory_honcho"]
    assert input_pipeline == {"serial": ["memory_honcho_recall", "base_input"], "parallel": []}
    assert output_pipeline == {"serial": ["base_output"], "parallel": ["memory_honcho_sync"]}

    conclude_slot = yaml.safe_load((core_path / "agent" / "tools" / "honcho_conclude" / "slot.yaml").read_text())
    search_slot = yaml.safe_load((core_path / "agent" / "tools" / "honcho_search" / "slot.yaml").read_text())
    assert conclude_slot["approval_policy"] == "prompt"
    assert conclude_slot["failure_policy"] == "soft"
    assert search_slot["approval_policy"] == "auto"
    assert search_slot["failure_policy"] == "soft"
    assert search_slot["risk"] == "medium"
    assert search_slot["display_policy"] == "summary"
    assert search_slot["model_output_policy"] == "content"

    data_dir = core_path / "memory" / "honcho"
    data_dir.mkdir(parents=True)
    (data_dir / "cache.json").write_text('{"kept": true}', encoding="utf-8")
    removed = manager.uninstall(core_id="assistant", package_id="memory_honcho")

    assert removed.action == "uninstall"
    assert not (core_path / "agent" / "bootstrap" / "memory_honcho").exists()
    assert not (core_path / "agent" / "input" / "memory_honcho_recall").exists()
    assert not (core_path / "agent" / "output" / "memory_honcho_sync").exists()
    assert not (core_path / "agent" / "lib" / "memory_honcho").exists()
    for tool in HONCHO_TOOLS:
        assert not (core_path / "agent" / "tools" / tool).exists()
    assert (data_dir / "cache.json").read_text(encoding="utf-8") == '{"kept": true}'
    assert yaml.safe_load((core_path / "agent" / "bootstrap" / "pipeline.yaml").read_text())["serial"] == [
        "session_context"
    ]
    assert yaml.safe_load((core_path / "agent" / "input" / "pipeline.yaml").read_text()) == {
        "serial": ["base_input"],
        "parallel": [],
    }
    assert yaml.safe_load((core_path / "agent" / "output" / "pipeline.yaml").read_text()) == {
        "serial": ["base_output"],
        "parallel": [],
    }


def test_memory_honcho_enable_tools_false_installs_no_honcho_tools(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    _manager(app).install(
        core_id="assistant",
        package_id="memory_honcho",
        option_answers={"enable_tools": False, "api_key": "test-key"},
    )

    core_path = app.version_store.active_core_path("assistant")
    assert (core_path / "agent" / "input" / "memory_honcho_recall" / "module.py").exists()
    for tool in HONCHO_TOOLS:
        assert not (core_path / "agent" / "tools" / tool).exists()


@pytest.mark.asyncio
async def test_memory_honcho_hybrid_injects_context_exposes_tools_and_syncs_turn(tmp_path, monkeypatch):
    state = _install_fake_honcho(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(
        core_id="assistant",
        package_id="memory_honcho",
        option_answers={"api_key": "test-key", "peer_name": "allen"},
    )
    provider = _RecordingProvider(default="assistant answer")
    app.runner.provider = provider

    result = await app.runner.run_turn("remember my project preference")
    await app.runner.drain_background_tasks()

    first_request_text = _request_text(provider)
    assert _delivery_text(result) == "assistant answer"
    assert "# Honcho Memory" in first_request_text
    assert "<memory-context>" in first_request_text
    assert "Summary for workspace" in first_request_text
    assert "User representation for query: remember my project preference" in first_request_text
    assert "User prefers Chinese replies." in first_request_text
    assert "AI representation for Demiurge assistant." in first_request_text
    assert {tool.name for tool in provider.requests[0].tools}.issuperset(HONCHO_TOOLS)
    assert {"peer": "allen", "content": "remember my project preference"} in state.messages
    assert {"peer": "demiurge-assistant", "content": "assistant answer"} in state.messages

    core_path = app.version_store.active_core_path("assistant")
    synced = json.loads((core_path / "memory" / "honcho" / "synced_turns.json").read_text(encoding="utf-8"))
    assert len(synced) == 1
    assert not (core_path / "memory" / "honcho" / "outbox.jsonl").exists()
    history = app.runner.session_store.read_messages(app.runner.session_id)
    assert all("<memory-context>" not in message.content for message in history)


@pytest.mark.asyncio
async def test_memory_honcho_tools_mode_skips_auto_recall_but_keeps_tools(tmp_path, monkeypatch):
    _install_fake_honcho(monkeypatch)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _manager(app).install(
        core_id="assistant",
        package_id="memory_honcho",
        option_answers={"recall_mode": "tools", "api_key": "test-key", "peer_name": "allen"},
    )
    provider = _RecordingProvider(default="tools only")
    app.runner.provider = provider

    await app.runner.run_turn("do not inject context")
    await app.runner.drain_background_tasks()

    first_request_text = _request_text(provider)
    assert "# Honcho Memory" in first_request_text
    assert "tools-only mode" in first_request_text
    assert "<memory-context>" not in first_request_text
    assert "Summary for" not in first_request_text
    assert {tool.name for tool in provider.requests[0].tools}.issuperset(HONCHO_TOOLS)


@pytest.mark.asyncio
async def test_memory_honcho_output_sync_marks_turn_once(tmp_path, monkeypatch):
    state = _install_fake_honcho(monkeypatch)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _manager(app).install(
        core_id="assistant",
        package_id="memory_honcho",
        option_answers={"api_key": "test-key", "peer_name": "allen"},
    )
    provider = _RecordingProvider(default="synced answer")
    app.runner.provider = provider

    result = await app.runner.run_turn("sync this turn")
    await app.runner.drain_background_tasks()

    core_path = app.version_store.active_core_path("assistant")
    synced_path = core_path / "memory" / "honcho" / "synced_turns.json"
    synced = json.loads(synced_path.read_text(encoding="utf-8"))
    assert result.turn_id in synced
    assert len([message for message in state.messages if message["content"] == "sync this turn"]) == 1
    assert len([message for message in state.messages if message["content"] == "synced answer"]) == 1

    monkeypatch.syspath_prepend(str(core_path / "agent" / "lib"))
    runtime = importlib.import_module("memory_honcho.runtime")
    config_module = importlib.import_module("memory_honcho.config")
    config = config_module.load_config(core_path / "agent" / "output" / "memory_honcho_sync" / "module.py")
    ctx = SimpleNamespace(
        turn=SimpleNamespace(
            turn_id=result.turn_id,
            session_id=app.runner.session_id,
            user_input=SimpleNamespace(content="sync this turn"),
            metadata={},
        ),
        input=SimpleNamespace(workspace=app.runner.workspace),
        output=SimpleNamespace(content="synced answer", workspace=app.runner.workspace),
    )

    second = runtime.sync_turn(ctx, config)

    assert second["enqueued"] is False
    assert len([message for message in state.messages if message["content"] == "sync this turn"]) == 1
    assert len([message for message in state.messages if message["content"] == "synced answer"]) == 1


@pytest.mark.asyncio
async def test_memory_honcho_tool_uses_fake_honcho_adapter(tmp_path, monkeypatch):
    _install_fake_honcho(monkeypatch)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _manager(app).install(
        core_id="assistant",
        package_id="memory_honcho",
        option_answers={"api_key": "test-key", "peer_name": "allen"},
    )
    provider = _RecordingProvider(
        responses=[
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="honcho_search_1",
                        name="honcho_search",
                        arguments={"query": "reply preference"},
                    )
                ]
            ),
            LLMResponse(content="tool done"),
        ]
    )
    app.runner.provider = provider

    result = await app.runner.run_turn("search memory")
    await app.runner.drain_background_tasks()

    tool_result = result.tool_results[0].result
    payload = json.loads(tool_result.content)
    assert result.tool_results[0].call.name == "honcho_search"
    assert tool_result.is_error is False
    assert payload["success"] is True
    assert "User representation for query: reply preference" in payload["result"]
    assert "Honcho search completed." in (tool_result.display_output or "")


@pytest.mark.asyncio
async def test_memory_honcho_missing_client_fails_open_and_tool_returns_error(tmp_path, monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    monkeypatch.delenv("HONCHO_BASE_URL", raising=False)
    monkeypatch.delitem(sys.modules, "honcho", raising=False)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _manager(app).install(core_id="assistant", package_id="memory_honcho")
    provider = _RecordingProvider(
        responses=[
            LLMResponse(content="no honcho"),
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="honcho_search_1",
                        name="honcho_search",
                        arguments={"query": "anything"},
                    )
                ]
            ),
            LLMResponse(content="after failed tool"),
        ]
    )
    app.runner.provider = provider

    first = await app.runner.run_turn("ordinary turn")
    await app.runner.drain_background_tasks()
    second = await app.runner.run_turn("call honcho tool")
    await app.runner.drain_background_tasks()

    assert _delivery_text(first) == "no honcho"
    assert "# Honcho Memory" in _request_text(provider, 0)
    assert "<memory-context>" not in _request_text(provider, 0)
    assert _delivery_text(second) == "after failed tool"
    tool_result = second.tool_results[0].result
    payload = json.loads(tool_result.content)
    assert tool_result.is_error is True
    assert payload["success"] is False
    assert "Honcho is not configured" in payload["error"]
    core_path = app.version_store.active_core_path("assistant")
    assert (core_path / "memory" / "honcho" / "outbox.jsonl").exists()
