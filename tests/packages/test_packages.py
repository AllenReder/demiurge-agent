import json
from pathlib import Path
from urllib.request import Request

import pytest
import yaml
from rich.console import Console

from demiurge.app import create_app
from demiurge.cli import main
from demiurge.package_wizard import PackageWizard
from demiurge.packages import (
    REDACTED_SECRET,
    PackageCatalog,
    PackageCatalogError,
    PackageManager,
    PackageOperationError,
    default_catalog_root,
)
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime
from demiurge.security.approval import ApprovalDecision
from demiurge.ui_gateway import TuiInteractionBridge


def _manager(app, catalog_root: Path | None = None) -> PackageManager:
    catalog = PackageCatalog.load(default_catalog_root(catalog_root))
    return PackageManager(version_store=app.version_store, catalog=catalog)


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body


class _EventSink:
    def __init__(self):
        self.items = []

    async def __call__(self, event, payload):
        self.items.append((event, payload))

    def text(self):
        values = []
        for event, payload in self.items:
            if event != "interaction.deliver":
                continue
            for delivery in payload.get("deliveries", []):
                values.append(delivery.get("text") or delivery.get("fallback_text") or "")
        return "\n".join(values)


class _RecordingBridge:
    def __init__(self):
        self.outbounds = []

    async def deliver(self, outbound):
        self.outbounds.append(outbound)
        outbound.mark_delivered()

    async def prompt_user(self, prompt):
        return ""

    async def request_approval(self, request):
        return ApprovalDecision("deny", "test bridge")

    @property
    def deliveries(self):
        return [delivery for outbound in self.outbounds for delivery in outbound.deliveries]


def _mock_minimax_http(
    monkeypatch,
    *,
    response: dict | None = None,
    response_audio: str | None = None,
    downloads: dict[str, bytes] | None = None,
) -> list[dict[str, object]]:
    monkeypatch.setenv("DEMIURGE_MINIMAX_API_KEY", "test-minimax-key")
    calls: list[dict[str, object]] = []
    audio = response_audio or b"MINIMAX-AUDIO".hex()
    response_payload = response or {
        "base_resp": {"status_code": 0, "status_msg": "ok"},
        "trace_id": "trace-test",
        "extra_info": {"audio_format": "mp3", "usage_characters": 12},
        "data": {"audio": audio},
    }

    def fake_urlopen(request: Request, timeout=60):
        url = request.full_url
        method = request.get_method()
        if method == "POST":
            payload = json.loads((request.data or b"{}").decode("utf-8"))
            calls.append({"url": url, "method": method, "json": payload})
            return _FakeHTTPResponse(json.dumps(response_payload).encode("utf-8"))
        assert method == "GET"
        calls.append({"url": url, "method": method})
        body = (downloads or {}).get(url)
        assert body is not None
        return _FakeHTTPResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_builtin_catalog_lists_minimax_tts_package():
    catalog = PackageCatalog.load(default_catalog_root())

    assert catalog.catalog.catalog_id == "demiurge_builtin"
    package = catalog.packages["minimax_tts"]
    assert {"audio", "tts", "provider:minimax"}.issubset(package.tags)
    assert [option.option_id for option in package.options] == ["mode", "enable_tool", "api_key"]
    mode = package.options[0]
    assert mode.description
    assert mode.choice_descriptions["direct"]
    assert mode.choice_descriptions["summary"]
    assert {component.kind for component in package.components} == {"lib", "output", "core", "tool", "skill"}


def test_builtin_catalog_lists_memory_basic_package():
    catalog = PackageCatalog.load(default_catalog_root())

    package = catalog.packages["memory_basic"]
    assert {"memory", "context"}.issubset(package.tags)
    assert {component.kind for component in package.components} == {"lib", "input", "tool"}
    assert [component.component_id for component in package.components] == [
        "memory_lib",
        "memory_context",
        "memory_tool",
    ]


def test_install_and_uninstall_memory_basic_preserves_data(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    result = manager.install(core_id="assistant", package_id="memory_basic")

    core_path = app.version_store.active_core_path("assistant")
    assert result.registry_path == core_path / "packages.yaml"
    assert (core_path / "agent" / "lib" / "memory_basic" / "store.py").exists()
    assert (core_path / "agent" / "input" / "memory_context" / "module.py").exists()
    assert (core_path / "agent" / "tools" / "memory" / "module.py").exists()
    assert not (core_path / "agent" / "skills" / "memory_policy").exists()
    assert not (core_path / "memory").exists()
    config = yaml.safe_load((core_path / "agent" / "lib" / "memory_basic" / "config.yaml").read_text())
    assert config["storage"] == {"relative_to": "core_root", "path": "memory"}
    assert config["snapshot"]["mode"] == "session"
    assert config["limits"] == {"memory_chars": 2200, "user_chars": 1375}
    pipeline = yaml.safe_load((core_path / "agent" / "input" / "pipeline.yaml").read_text())
    assert pipeline["serial"][:2] == ["memory_context", "base_input"]
    slot = yaml.safe_load((core_path / "agent" / "tools" / "memory" / "slot.yaml").read_text())
    assert slot["risk"] == "medium"
    assert slot["approval_policy"] == "auto"
    assert slot["display_policy"] == "summary"
    assert slot["model_output_policy"] == "content"
    assert set(slot["input_schema"]["properties"]["action"]["enum"]) == {"add", "replace", "remove", "list"}
    assert set(slot["input_schema"]["properties"]["target"]["enum"]) == {"memory", "user", "all"}

    memory_dir = core_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "USER.md").write_text("User prefers concise Chinese replies.", encoding="utf-8")
    removed = manager.uninstall(core_id="assistant", package_id="memory_basic")

    assert removed.action == "uninstall"
    assert not (core_path / "agent" / "input" / "memory_context").exists()
    assert not (core_path / "agent" / "tools" / "memory").exists()
    assert not (core_path / "agent" / "lib" / "memory_basic").exists()
    assert (memory_dir / "USER.md").read_text(encoding="utf-8") == "User prefers concise Chinese replies."
    pipeline = yaml.safe_load((core_path / "agent" / "input" / "pipeline.yaml").read_text())
    assert pipeline["serial"] == ["base_input"]


def test_catalog_rejects_component_source_escape(tmp_path):
    root = tmp_path / "catalog"
    (root / "packages").mkdir(parents=True)
    (root / "catalog.yaml").write_text("id: test\n", encoding="utf-8")
    (root / "packages" / "bad.yaml").write_text(
        "id: bad\n"
        "components:\n"
        "  - id: bad\n"
        "    kind: output\n"
        "    source: ../bad\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageCatalogError, match="component source must stay inside"):
        PackageCatalog.load(root)


def test_install_and_uninstall_minimax_direct_package(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    result = manager.install(core_id="assistant", package_id="minimax_tts")

    core_path = app.version_store.active_core_path("assistant")
    assert result.registry_path == core_path / "packages.yaml"
    assert (core_path / "agent" / "lib" / "tts_minimax" / "synthesizer.py").exists()
    assert (core_path / "agent" / "output" / "tts_minimax" / "module.py").exists()
    assert not (core_path / "agent" / "tools" / "tts_synthesize").exists()
    lib_config_text = (core_path / "agent" / "lib" / "tts_minimax" / "config.yaml").read_text()
    lib_config = yaml.safe_load(lib_config_text)
    assert lib_config["provider"] == "tts_minimax"
    assert lib_config["api_key_env"] == "DEMIURGE_MINIMAX_API_KEY"
    assert "emotion" not in lib_config["voice_setting"]
    assert lib_config["audio_setting"] == {}
    assert "\\u8BED" not in lib_config_text
    output_config = yaml.safe_load((core_path / "agent" / "output" / "tts_minimax" / "config.yaml").read_text())
    assert output_config["summarizer_core"] is None
    assert output_config["summary"] == "MiniMax TTS audio"
    assert "provider" not in output_config
    assert "api_key_env" not in output_config
    pipeline = yaml.safe_load((core_path / "agent" / "output" / "pipeline.yaml").read_text())
    assert pipeline["serial"] == ["base_output"]
    assert pipeline["parallel"] == ["tts_minimax"]
    registry = yaml.safe_load((core_path / "packages.yaml").read_text())
    assert registry["schema_version"] == 2
    assert registry["installed"][0]["package_id"] == "minimax_tts"
    assert registry["installed"][0]["options"]["api_key"] is None
    assert "config" not in registry["installed"][0]["components"][0]

    removed = manager.uninstall(core_id="assistant", package_id="minimax_tts")

    assert removed.action == "uninstall"
    assert not (core_path / "agent" / "output" / "tts_minimax").exists()
    assert not (core_path / "agent" / "lib" / "tts_minimax").exists()
    pipeline = yaml.safe_load((core_path / "agent" / "output" / "pipeline.yaml").read_text())
    assert pipeline["serial"] == ["base_output"]
    assert pipeline["parallel"] == []
    assert not (core_path / "packages.yaml").exists()


def test_install_summary_mode_copies_child_core_and_config(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    manager.install(core_id="assistant", package_id="minimax_tts", option_answers={"mode": "summary"})

    core_path = app.version_store.active_core_path("assistant")
    child_core = app.version_store.active_core_path("tts_summarizer")
    assert (child_core / "agent.yaml").exists()
    assert yaml.safe_load((child_core / "agent.yaml").read_text())["agent"]["id"] == "tts_summarizer"
    config = yaml.safe_load((core_path / "agent" / "output" / "tts_minimax" / "config.yaml").read_text())
    assert config["summarizer_core"] == "tts_summarizer"


def test_install_writes_option_answers_to_config_and_redacts_registry(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    result = manager.install(core_id="assistant", package_id="minimax_tts", option_answers={"api_key": "secret-value"})

    core_path = app.version_store.active_core_path("assistant")
    config = yaml.safe_load((core_path / "agent" / "lib" / "tts_minimax" / "config.yaml").read_text())
    assert config["api_key"] == "secret-value"
    registry = yaml.safe_load((core_path / "packages.yaml").read_text())
    record = registry["installed"][0]
    assert record["options"]["api_key"] == REDACTED_SECRET
    assert "config" not in result.components[0]


def test_package_options_validate_required_defaults_and_choices(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_option_catalog(catalog_root, required_default=False)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    with pytest.raises(PackageOperationError, match="run `demiurge package`"):
        manager.install(core_id="assistant", package_id="voice")
    with pytest.raises(PackageOperationError, match="must be one of"):
        manager.install(core_id="assistant", package_id="voice", option_answers={"voice": "bad"})

    manager.install(core_id="assistant", package_id="voice", option_answers={"voice": "alto"})
    config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "output" / "voice" / "config.yaml").read_text()
    )
    assert config["voice"] == "alto"


def test_package_options_use_defaults_for_noninteractive_install(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_option_catalog(catalog_root, required_default=True)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    manager.install(core_id="assistant", package_id="voice")

    config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "output" / "voice" / "config.yaml").read_text()
    )
    assert config["voice"] == "alto"


def test_install_rejects_existing_unmanaged_target(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_test_catalog(catalog_root)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    manager.install(core_id="assistant", package_id="first")

    with pytest.raises(RuntimeError, match="target already exists"):
        manager.install(core_id="assistant", package_id="second")


def test_shared_lib_source_is_reused_and_pruned_last(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_shared_lib_catalog(catalog_root)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    first = manager.install(core_id="assistant", package_id="first")
    second = manager.install(core_id="assistant", package_id="second")

    core_path = app.version_store.active_core_path("assistant")
    assert first.components[0]["reused"] is False
    assert second.components[0]["reused"] is True
    assert (core_path / "agent" / "lib" / "shared").exists()

    removed = manager.uninstall(core_id="assistant", package_id="first")
    assert "kept shared target" in removed.warnings[0]
    assert (core_path / "agent" / "lib" / "shared").exists()

    manager.uninstall(core_id="assistant", package_id="second")
    assert not (core_path / "agent" / "lib" / "shared").exists()


def test_cli_package_list_and_install(tmp_path, capsys):
    home = tmp_path / "home"

    main(["--home", str(home), "package", "list", "--tag", "tts", "--json"])
    listed = json.loads(capsys.readouterr().out)
    minimax = next(package for package in listed["packages"] if package["id"] == "minimax_tts")
    mode = next(option for option in minimax["options"] if option["id"] == "mode")
    assert mode["description"]
    assert mode["choice_descriptions"]["summary"]
    assert "tts" in listed["tags"]

    main(
        [
            "--home",
            str(home),
            "package",
            "install",
            "minimax_tts",
            "--core",
            "assistant",
            "--preview",
            "--json",
        ]
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["preview"] is True
    assert preview["package_id"] == "minimax_tts"
    assert not (home / "agents" / "assistant" / "agent" / "output" / "tts_minimax").exists()

    main(
        [
            "--home",
            str(home),
            "package",
            "install",
            "minimax_tts",
            "--core",
            "assistant",
            "--option",
            "mode=summary",
            "--option",
            "enable_tool=true",
            "--json",
        ]
    )
    installed = json.loads(capsys.readouterr().out)
    assert installed["action"] == "install"
    assert installed["preview"] is False
    assert installed["package_id"] == "minimax_tts"
    assert (home / "agents" / "assistant" / "agent" / "output" / "tts_minimax").exists()
    assert (home / "agents" / "assistant" / "agent" / "tools" / "text_to_speech").exists()
    assert not (home / "agents" / "assistant" / "agent" / "tools" / "tts_synthesize").exists()
    assert (home / "agents" / "tts_summarizer" / "agent.yaml").exists()

    main(["--home", str(home), "package", "uninstall", "minimax_tts", "--core", "assistant", "--preview", "--json"])
    uninstall_preview = json.loads(capsys.readouterr().out)
    assert uninstall_preview["preview"] is True
    assert uninstall_preview["components"][0]["remove"] is True
    assert (home / "agents" / "assistant" / "agent" / "output" / "tts_minimax").exists()


def test_cli_package_without_subcommand_runs_interactive_wizard(tmp_path, monkeypatch):
    calls = []

    def fake_wizard(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("demiurge.cli.run_package_wizard", fake_wizard)

    main(["--home", str(tmp_path / "home"), "package"])

    assert calls
    assert calls[0]["default_core_id"] == "assistant"


def test_wizard_installs_with_option_answers(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)
    prompt = _FakePrompt(
        selections=["assistant", "all", "minimax_tts", "install", "direct", "back", "exit"],
        confirms=[False, True],
        inputs=["wizard-secret"],
    )
    console = Console(record=True)

    PackageWizard(manager=manager, version_store=app.version_store, console=console, prompt=prompt).run()

    config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "lib" / "tts_minimax" / "config.yaml").read_text()
    )
    assert config["api_key"] == "wizard-secret"
    registry = yaml.safe_load((app.version_store.active_core_path("assistant") / "packages.yaml").read_text())
    assert registry["installed"][0]["options"]["api_key"] == REDACTED_SECRET
    main_menu = next(call for call in prompt.select_calls if call["title"] == "Package manager")
    assert [choice.value for choice in main_menu["choices"][:3]] == ["all", "search", "tags"]
    mode_select = next(call for call in prompt.select_calls if call["title"] == "TTS mode")
    assert mode_select["choices"][0].description
    assert mode_select["choices"][1].description


def test_wizard_uninstalls_installed_package(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)
    manager.install(core_id="assistant", package_id="minimax_tts")
    prompt = _FakePrompt(
        selections=["assistant", "installed", "minimax_tts", "uninstall", "exit"],
        confirms=[True],
    )

    PackageWizard(manager=manager, version_store=app.version_store, console=Console(record=True), prompt=prompt).run()

    assert not (app.version_store.active_core_path("assistant") / "agent" / "output" / "tts_minimax").exists()


@pytest.mark.asyncio
async def test_tui_packages_command_lists_details_and_installs(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    sink = _EventSink()
    bridge = TuiInteractionBridge(app, emit=sink)

    assert (await bridge.command("/packages"))["handled"] is True
    assert (await bridge.command("/packages minimax_tts"))["handled"] is True
    assert (await bridge.command("/packages install minimax_tts"))["handled"] is True

    output = sink.text()
    assert "minimax_tts" in output
    assert "Package: minimax_tts" in output
    assert "installed minimax_tts for assistant" in output


@pytest.mark.asyncio
async def test_minimax_direct_mode_delivers_hex_audio_from_parent_output(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", package_id="minimax_tts")
    calls = _mock_minimax_http(monkeypatch)
    bridge = _RecordingBridge()

    await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="hello voice", source="local", conversation_key="pkg:test"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    audio_block = next(block for delivery in bridge.deliveries for block in delivery.blocks if block.get("type") == "audio")
    assert audio_block["artifact"]["media_type"] == "audio/mpeg"
    assert audio_block["artifact"]["metadata"]["provider"] == "tts_minimax"
    assert calls[0]["json"]["stream"] is False
    assert "audio_setting" not in calls[0]["json"]
    assert "output_format" not in calls[0]["json"]
    assert "hello voice" in calls[0]["json"]["text"]
    assert (workspace / ".demiurge-tts").exists()
    assert next(workspace.glob(".demiurge-tts/*.mp3")).read_bytes() == b"MINIMAX-AUDIO"


@pytest.mark.asyncio
async def test_minimax_summary_mode_uses_child_result_then_parent_delivers_audio(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", package_id="minimax_tts", option_answers={"mode": "summary"})
    calls = _mock_minimax_http(monkeypatch)
    bridge = _RecordingBridge()

    await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="summarize voice", source="local", conversation_key="pkg:test"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    audio_delivery = next(
        delivery for delivery in bridge.deliveries if any(block.get("type") == "audio" for block in delivery.blocks)
    )
    assert audio_delivery.history_policy == "transient"
    assert "[fake] [fake] summarize voice" in calls[0]["json"]["text"]
    assert next(iter(workspace.glob(".demiurge-tts/*.mp3"))).read_bytes() == b"MINIMAX-AUDIO"


@pytest.mark.asyncio
async def test_minimax_tool_generates_audio_with_shared_lib(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = tmp_path / "tts_tool.json"
    script.write_text(
        json.dumps(
            [
                {"tool_calls": [{"id": "tts_tool", "name": "text_to_speech", "arguments": {"text": "tool voice"}}]},
                {"content": ""},
            ]
        ),
        encoding="utf-8",
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", fake_script=script, workspace=workspace)
    manager = _manager(app)
    manager.install(core_id="assistant", package_id="minimax_tts", option_answers={"enable_tool": True})
    calls = _mock_minimax_http(monkeypatch)

    result = await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="make tool voice", source="local", conversation_key="pkg:test")
    )

    assert (app.version_store.active_core_path("assistant") / "agent" / "tools" / "text_to_speech").exists()
    assert not (app.version_store.active_core_path("assistant") / "agent" / "tools" / "tts_synthesize").exists()
    tool_config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "tools" / "text_to_speech" / "config.yaml").read_text()
    )
    assert tool_config == {"filename_template": "{turn_id}-tool.{format}"}
    assert "tool voice" in calls[0]["json"]["text"]
    audio_delivery = next(
        delivery for delivery in result.deliveries if any(block.get("type") == "audio" for block in delivery.blocks)
    )
    assert audio_delivery.history_policy == "transient"
    assert audio_delivery.metadata["slot"] == "agent/tools/text_to_speech"
    assert result.tool_results[0].call.name == "text_to_speech"
    assert result.tool_results[0].result.content == "sent audio"
    messages = app.runner.session_store.read_messages(app.runner.session_id)
    tool_message = next(message for message in messages if message.role == "tool")
    assert tool_message.content == "Sent speech audio to the user."
    assert tool_message.model_visible is True
    assert next(workspace.glob(".demiurge-tts/*-tool.mp3")).read_bytes() == b"MINIMAX-AUDIO"


@pytest.mark.asyncio
async def test_tts_minimax_url_output_downloads_audio(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", package_id="minimax_tts")
    config_path = app.version_store.active_core_path("assistant") / "agent" / "lib" / "tts_minimax" / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["output_format"] = "url"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    calls = _mock_minimax_http(
        monkeypatch,
        response_audio="https://cdn.example.test/audio.mp3",
        downloads={"https://cdn.example.test/audio.mp3": b"URL-AUDIO"},
    )
    bridge = _RecordingBridge()

    await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="hello url voice", source="local", conversation_key="pkg:test"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    assert calls[0]["json"]["output_format"] == "url"
    assert next(workspace.glob(".demiurge-tts/*.mp3")).read_bytes() == b"URL-AUDIO"
    assert any(block.get("type") == "audio" for delivery in bridge.deliveries for block in delivery.blocks)


@pytest.mark.asyncio
async def test_tts_minimax_api_error_keeps_base_output_without_audio(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", package_id="minimax_tts")
    _mock_minimax_http(
        monkeypatch,
        response={
            "base_resp": {"status_code": 1001, "status_msg": "bad request"},
            "data": {},
        },
    )
    bridge = _RecordingBridge()

    await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="hello failure", source="local", conversation_key="pkg:test"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    assert bridge.deliveries
    assert not any(block.get("type") == "audio" for delivery in bridge.deliveries for block in delivery.blocks)
    assert any(
        event["type"] == "module.failed" and event["slot"] == "agent/output/tts_minimax"
        for event in app.runner.event_log.tail(50)
    )


def _write_test_catalog(root: Path) -> None:
    (root / "packages").mkdir(parents=True)
    for component in ("first", "second"):
        slot = root / "output" / component
        slot.mkdir(parents=True)
        (slot / "slot.yaml").write_text(
            "entrypoint: module:process\n"
            "description: test output\n"
            "failure_policy: soft\n"
            "capabilities:\n"
            "  []\n",
            encoding="utf-8",
        )
        (slot / "module.py").write_text("def process(ctx):\n    pass\n", encoding="utf-8")
    (root / "catalog.yaml").write_text("id: test_catalog\nname: Test\n", encoding="utf-8")
    for package_id, source in (("first", "first"), ("second", "second")):
        (root / "packages" / f"{package_id}.yaml").write_text(
            f"id: {package_id}\n"
            "tags:\n"
            "  - tts\n"
            "components:\n"
            f"  - id: {package_id}\n"
            "    kind: output\n"
            f"    source: {source}\n"
            "    target: agent/output/shared_voice\n"
            "    pipeline:\n"
            "      group: serial\n"
            "      after: base_output\n",
            encoding="utf-8",
        )


def _write_shared_lib_catalog(root: Path) -> None:
    lib = root / "lib" / "shared"
    lib.mkdir(parents=True)
    (lib / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "packages").mkdir(parents=True)
    (root / "catalog.yaml").write_text("id: shared_catalog\nname: Shared\n", encoding="utf-8")
    for package_id in ("first", "second"):
        (root / "packages" / f"{package_id}.yaml").write_text(
            f"id: {package_id}\n"
            "components:\n"
            "  - id: shared\n"
            "    kind: lib\n"
            "    source: shared\n"
            "    target: agent/lib/shared\n",
            encoding="utf-8",
        )


class _FakePrompt:
    def __init__(
        self,
        *,
        selections: list[str] | None = None,
        confirms: list[bool] | None = None,
        inputs: list[str] | None = None,
    ) -> None:
        self.selections = list(selections or [])
        self.confirms = list(confirms or [])
        self.inputs = list(inputs or [])
        self.select_calls = []

    def select(self, title, choices, *, default_index=0):
        self.select_calls.append({"title": title, "choices": list(choices), "default_index": default_index})
        if not self.selections:
            return choices[default_index].value
        value = self.selections.pop(0)
        assert value in {choice.value for choice in choices}
        return value

    def confirm(self, message, *, default=False):
        if not self.confirms:
            return default
        return self.confirms.pop(0)

    def input(self, message, *, default=None, secret=False):
        if not self.inputs:
            return default or ""
        return self.inputs.pop(0)


def _write_option_catalog(root: Path, *, required_default: bool) -> None:
    slot = root / "output" / "voice"
    slot.mkdir(parents=True)
    (root / "packages").mkdir(parents=True)
    (slot / "slot.yaml").write_text(
        "entrypoint: module:process\n"
        "description: option output\n"
        "failure_policy: soft\n"
        "capabilities:\n"
        "  []\n",
        encoding="utf-8",
    )
    (slot / "module.py").write_text("def process(ctx):\n    pass\n", encoding="utf-8")
    (root / "catalog.yaml").write_text("id: options_catalog\nname: Options\n", encoding="utf-8")
    default_line = "    default: alto\n" if required_default else ""
    (root / "packages" / "voice.yaml").write_text(
        "id: voice\n"
        "tags:\n"
        "  - voice\n"
        "options:\n"
        "  - id: voice\n"
        "    type: choice\n"
        "    prompt: Voice\n"
        "    description: Select the voice used for generated audio.\n"
        "    required: true\n"
        f"{default_line}"
        "    choices:\n"
        "      - value: alto\n"
        "        description: Higher register voice.\n"
        "      - bass\n"
        "components:\n"
        "  - id: voice\n"
        "    kind: output\n"
        "    source: voice\n"
        "    target: agent/output/voice\n"
        "    pipeline:\n"
        "      group: serial\n"
        "      after: base_output\n"
        "    config:\n"
        "      voice: ${options.voice}\n",
        encoding="utf-8",
    )
