import json
from pathlib import Path
from urllib.request import Request

import pytest
import yaml

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
from demiurge.ui_gateway import TuiInteractionBridge
from rich.console import Console


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


def test_builtin_catalog_lists_tts_presets():
    catalog = PackageCatalog.load(default_catalog_root())

    assert catalog.catalog.catalog_id == "demiurge_builtin"
    assert {"tts_only", "tts_summary"}.issubset(catalog.presets)
    assert catalog.presets["tts_only"].options[0].option_id == "api_key"
    assert catalog.presets["tts_only"].writes[0].path == "config.api_key"
    assert catalog.presets["tts_only"].writes[0].component_id == "tts_minimax"
    assert catalog.features["tts"].tags["tts"]["conflict"] == "advisory"


def test_catalog_rejects_component_source_escape(tmp_path):
    root = tmp_path / "catalog"
    (root / "features").mkdir(parents=True)
    (root / "presets").mkdir()
    (root / "catalog.yaml").write_text("id: test\n", encoding="utf-8")
    (root / "features" / "tts.yaml").write_text("id: tts\n", encoding="utf-8")
    (root / "presets" / "bad.yaml").write_text(
        "id: bad\n"
        "feature: tts\n"
        "components:\n"
        "  - id: bad\n"
        "    kind: output\n"
        "    source: ../bad\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageCatalogError, match="component source must stay inside"):
        PackageCatalog.load(root)


def test_install_and_uninstall_tts_only_preset(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    result = manager.install(core_id="assistant", preset_id="tts_only")

    core_path = app.version_store.active_core_path("assistant")
    assert result.registry_path == core_path / "packages.yaml"
    assert (core_path / "agent" / "output" / "tts_minimax" / "module.py").exists()
    config_text = (core_path / "agent" / "output" / "tts_minimax" / "config.yaml").read_text()
    config = yaml.safe_load(config_text)
    assert config["summarizer_core"] is None
    assert config["provider"] == "tts_minimax"
    assert config["api_key_env"] == "DEMIURGE_MINIMAX_API_KEY"
    assert "emotion" not in config["voice_setting"]
    assert config["audio_setting"] == {}
    assert "caption:" not in config_text
    assert "\\u8BED" not in config_text
    pipeline = yaml.safe_load((core_path / "agent" / "output" / "pipeline.yaml").read_text())
    assert pipeline["serial"] == ["base_output", "tts_minimax"]
    registry = yaml.safe_load((core_path / "packages.yaml").read_text())
    assert registry["installed"][0]["preset_id"] == "tts_only"
    assert registry["installed"][0]["options"]["api_key"] is None

    removed = manager.uninstall(core_id="assistant", preset_id="tts_only")

    assert removed.action == "uninstall"
    assert not (core_path / "agent" / "output" / "tts_minimax").exists()
    pipeline = yaml.safe_load((core_path / "agent" / "output" / "pipeline.yaml").read_text())
    assert pipeline["serial"] == ["base_output"]
    assert not (core_path / "packages.yaml").exists()


def test_install_summary_preset_copies_child_core_and_config(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    manager.install(core_id="assistant", preset_id="tts_summary")

    core_path = app.version_store.active_core_path("assistant")
    child_core = app.version_store.active_core_path("tts_summarizer")
    assert (child_core / "agent.yaml").exists()
    assert yaml.safe_load((child_core / "agent.yaml").read_text())["agent"]["id"] == "tts_summarizer"
    config = yaml.safe_load((core_path / "agent" / "output" / "tts_minimax" / "config.yaml").read_text())
    assert config["summarizer_core"] == "tts_summarizer"


def test_install_writes_option_answers_to_config_and_redacts_registry(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    result = manager.install(core_id="assistant", preset_id="tts_only", option_answers={"api_key": "secret-value"})

    core_path = app.version_store.active_core_path("assistant")
    config = yaml.safe_load((core_path / "agent" / "output" / "tts_minimax" / "config.yaml").read_text())
    assert config["api_key"] == "secret-value"
    registry = yaml.safe_load((core_path / "packages.yaml").read_text())
    record = registry["installed"][0]
    assert record["options"]["api_key"] == REDACTED_SECRET
    assert "config" not in record["components"][0]
    assert "config" not in result.components[0]


def test_package_options_validate_required_defaults_and_choices(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_option_catalog(catalog_root, required_default=False)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    with pytest.raises(PackageOperationError, match="run `demiurge package`"):
        manager.install(core_id="assistant", preset_id="voice")
    with pytest.raises(PackageOperationError, match="must be one of"):
        manager.install(core_id="assistant", preset_id="voice", option_answers={"voice": "bad"})

    manager.install(core_id="assistant", preset_id="voice", option_answers={"voice": "alto"})
    config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "output" / "voice" / "config.yaml").read_text()
    )
    assert config["voice"] == "alto"


def test_package_options_use_defaults_for_noninteractive_install(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_option_catalog(catalog_root, required_default=True)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    manager.install(core_id="assistant", preset_id="voice")

    config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "output" / "voice" / "config.yaml").read_text()
    )
    assert config["voice"] == "alto"


def test_install_rejects_existing_target(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)

    manager.install(core_id="assistant", preset_id="tts_only")

    with pytest.raises(RuntimeError, match="target already exists"):
        manager.install(core_id="assistant", preset_id="tts_summary")


def test_tag_conflict_is_warning_not_blocker(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_test_catalog(catalog_root)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)

    first = manager.install(core_id="assistant", preset_id="first")
    second = manager.install(core_id="assistant", preset_id="second")

    assert first.warnings == []
    assert "shares tag(s) tts" in second.warnings[0]
    assert (app.version_store.active_core_path("assistant") / "agent" / "output" / "second").exists()


def test_cli_package_list_and_install(tmp_path, capsys):
    home = tmp_path / "home"

    main(["--home", str(home), "package", "list", "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert "tts_only" in {preset["id"] for preset in listed["presets"]}

    main(["--home", str(home), "package", "install", "tts_only", "--core", "assistant", "--json"])
    installed = json.loads(capsys.readouterr().out)
    assert installed["action"] == "install"
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
        selections=["assistant", "browse", "tts_only", "install", "back", "exit"],
        inputs=["wizard-secret"],
    )
    console = Console(record=True)

    PackageWizard(manager=manager, version_store=app.version_store, console=console, prompt=prompt).run()

    config = yaml.safe_load(
        (app.version_store.active_core_path("assistant") / "agent" / "output" / "tts_minimax" / "config.yaml").read_text()
    )
    assert config["api_key"] == "wizard-secret"
    registry = yaml.safe_load((app.version_store.active_core_path("assistant") / "packages.yaml").read_text())
    assert registry["installed"][0]["options"]["api_key"] == REDACTED_SECRET


def test_wizard_uninstalls_installed_package(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app)
    manager.install(core_id="assistant", preset_id="tts_only")
    prompt = _FakePrompt(
        selections=["assistant", "installed", "tts_only", "uninstall", "exit"],
        confirms=[True],
    )

    PackageWizard(manager=manager, version_store=app.version_store, console=Console(record=True), prompt=prompt).run()

    assert not (app.version_store.active_core_path("assistant") / "agent" / "output" / "tts_minimax").exists()


def test_wizard_tag_conflict_cancel_and_confirm(tmp_path):
    catalog_root = tmp_path / "catalog"
    _write_test_catalog(catalog_root)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manager = _manager(app, catalog_root)
    manager.install(core_id="assistant", preset_id="first")

    cancel_prompt = _FakePrompt(
        selections=["assistant", "browse", "second", "install", "back", "exit"],
        confirms=[False],
    )
    PackageWizard(manager=manager, version_store=app.version_store, console=Console(record=True), prompt=cancel_prompt).run()
    assert not (app.version_store.active_core_path("assistant") / "agent" / "output" / "second").exists()

    confirm_prompt = _FakePrompt(
        selections=["assistant", "browse", "second", "install", "back", "exit"],
        confirms=[True],
    )
    PackageWizard(manager=manager, version_store=app.version_store, console=Console(record=True), prompt=confirm_prompt).run()
    assert (app.version_store.active_core_path("assistant") / "agent" / "output" / "second").exists()


@pytest.mark.asyncio
async def test_tui_packages_command_lists_details_and_installs(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    sink = _EventSink()
    bridge = TuiInteractionBridge(app, emit=sink)

    assert (await bridge.command("/packages"))["handled"] is True
    assert (await bridge.command("/packages tts_only"))["handled"] is True
    assert (await bridge.command("/packages install tts_only"))["handled"] is True

    output = sink.text()
    assert "tts_only" in output
    assert "Package preset: tts_only" in output
    assert "installed tts_only for assistant" in output


@pytest.mark.asyncio
async def test_tts_only_preset_delivers_hex_audio_from_parent_output(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", preset_id="tts_only")
    calls = _mock_minimax_http(monkeypatch)

    result = await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="hello voice", source="local", conversation_key="pkg:test")
    )

    audio_block = next(block for delivery in result.deliveries for block in delivery.blocks if block.get("type") == "audio")
    assert audio_block["artifact"]["media_type"] == "audio/mpeg"
    assert audio_block["artifact"]["metadata"]["provider"] == "tts_minimax"
    assert calls[0]["json"]["stream"] is False
    assert "audio_setting" not in calls[0]["json"]
    assert "output_format" not in calls[0]["json"]
    assert "hello voice" in calls[0]["json"]["text"]
    assert (workspace / ".demiurge-tts").exists()
    assert next(workspace.glob(".demiurge-tts/*.mp3")).read_bytes() == b"MINIMAX-AUDIO"
    result.mark_delivered()


@pytest.mark.asyncio
async def test_tts_summary_preset_uses_child_result_then_parent_delivers_audio(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", preset_id="tts_summary")
    calls = _mock_minimax_http(monkeypatch)

    result = await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="summarize voice", source="local", conversation_key="pkg:test")
    )

    audio_delivery = next(
        delivery for delivery in result.deliveries if any(block.get("type") == "audio" for block in delivery.blocks)
    )
    assert audio_delivery.history_policy == "transient"
    assert "[fake] [fake] summarize voice" in calls[0]["json"]["text"]
    assert next(iter(workspace.glob(".demiurge-tts/*.mp3"))).read_bytes() == b"MINIMAX-AUDIO"
    result.mark_delivered()


@pytest.mark.asyncio
async def test_tts_minimax_url_output_downloads_audio(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", preset_id="tts_only")
    config_path = app.version_store.active_core_path("assistant") / "agent" / "output" / "tts_minimax" / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["output_format"] = "url"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    calls = _mock_minimax_http(
        monkeypatch,
        response_audio="https://cdn.example.test/audio.mp3",
        downloads={"https://cdn.example.test/audio.mp3": b"URL-AUDIO"},
    )

    result = await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="hello url voice", source="local", conversation_key="pkg:test")
    )

    assert calls[0]["json"]["output_format"] == "url"
    assert next(workspace.glob(".demiurge-tts/*.mp3")).read_bytes() == b"URL-AUDIO"
    assert any(block.get("type") == "audio" for delivery in result.deliveries for block in delivery.blocks)
    result.mark_delivered()


@pytest.mark.asyncio
async def test_tts_minimax_api_error_keeps_base_output_without_audio(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _manager(app).install(core_id="assistant", preset_id="tts_only")
    _mock_minimax_http(
        monkeypatch,
        response={
            "base_resp": {"status_code": 1001, "status_msg": "bad request"},
            "data": {},
        },
    )

    result = await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="hello failure", source="local", conversation_key="pkg:test")
    )

    assert result.deliveries
    assert not any(block.get("type") == "audio" for delivery in result.deliveries for block in delivery.blocks)
    assert any(
        event["type"] == "module.failed" and event["slot"] == "agent/output/tts_minimax"
        for event in app.runner.event_log.tail(50)
    )
    result.mark_delivered()


def _write_test_catalog(root: Path) -> None:
    (root / "features").mkdir(parents=True)
    (root / "presets").mkdir()
    for component in ("first", "second"):
        slot = root / "components" / "output" / component
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
    (root / "features" / "tts.yaml").write_text(
        "id: tts\n"
        "name: TTS\n"
        "tags:\n"
        "  tts:\n"
        "    conflict: advisory\n",
        encoding="utf-8",
    )
    for preset in ("first", "second"):
        (root / "presets" / f"{preset}.yaml").write_text(
            f"id: {preset}\n"
            "feature: tts\n"
            "tags:\n"
            "  - tts\n"
            "components:\n"
            f"  - id: {preset}\n"
            "    kind: output\n"
            f"    source: {preset}\n"
            f"    target: agent/output/{preset}\n"
            "    pipeline:\n"
            "      group: serial\n"
            "      after: base_output\n",
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

    def select(self, title, choices, *, default_index=0):
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
    slot = root / "components" / "output" / "voice"
    slot.mkdir(parents=True)
    (root / "features").mkdir(parents=True)
    (root / "presets").mkdir()
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
    (root / "features" / "tts.yaml").write_text("id: tts\nname: TTS\n", encoding="utf-8")
    default_line = "    default: alto\n" if required_default else ""
    (root / "presets" / "voice.yaml").write_text(
        "id: voice\n"
        "feature: tts\n"
        "tags:\n"
        "  - voice\n"
        "options:\n"
        "  - id: voice\n"
        "    type: choice\n"
        "    prompt: Voice\n"
        "    required: true\n"
        f"{default_line}"
        "    choices:\n"
        "      - alto\n"
        "      - bass\n"
        "writes:\n"
        "  voice:\n"
        "    component: voice\n"
        "    path: config.voice\n"
        "components:\n"
        "  - id: voice\n"
        "    kind: output\n"
        "    source: voice\n"
        "    target: agent/output/voice\n"
        "    pipeline:\n"
        "      group: serial\n"
        "      after: base_output\n"
        "    config:\n"
        "      voice: null\n",
        encoding="utf-8",
    )
