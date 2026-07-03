import shutil

import pytest
import yaml

from demiurge.app import create_app, init_runtime, load_agent_fallback, load_host_config, source_agents_root, write_default_host_config_if_missing
from demiurge.cli import main
from demiurge.storage import VersionStore


def test_init_runtime_copies_fallback_assistant_and_evolver(tmp_path):
    home = tmp_path / "home"

    result = init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())

    assert result["core_id"] == "assistant"
    assert result["host_config"] == str(home / "config.yaml")
    assert result["host_config_created"] is True
    assert yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) == {
        "runtime": {"default_core": "assistant", "timezone": None},
        "channel": {"busy_mode": "interrupt"},
        "ui": {"user_message_align": "left", "demiurge_theme_color": "ff9afc", "user_theme_color": "9cc9ff"},
        "debug": {"show_system_prompt": False},
        "providers": {"default": None, "profiles": {}},
        "packages": {"repositories": {"builtin": {"type": "builtin"}}},
    }
    assert (home / "agents" / "agent.yaml").exists()
    assert (home / "agents" / "assistant" / "agent.yaml").exists()
    assert (home / "agents" / "evolver" / "agent.yaml").exists()
    assert result["fallback_config"] == str(home / "agents" / "agent.yaml")
    assert result["evolver_active_path"] == str(home / "agents" / "evolver")


def test_init_runtime_with_evolver_core_does_not_initialize_twice(tmp_path):
    home = tmp_path / "home"

    result = init_runtime(home=home, core_id="evolver", agents_root=source_agents_root())

    assert result["core_id"] == "evolver"
    assert result["active_path"] == str(home / "agents" / "evolver")
    assert result["previous_revision"] is None
    assert (home / ".core.git").exists()
    assert not (home / "history").exists()


def test_init_runtime_backs_up_existing_active_before_overwrite(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    active_instructions = home / "agents" / "assistant" / "agent" / "SOUL.md"
    active_instructions.write_text("custom active", encoding="utf-8")

    result = init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())

    assert result["previous_revision"] is None
    assert active_instructions.read_text(encoding="utf-8") == "custom active"
    assert not (home / "history").exists()


def test_init_runtime_backs_up_existing_fallback_before_overwrite(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    fallback = home / "agents" / "agent.yaml"
    fallback.write_text("model:\n  model_name: custom-model\n", encoding="utf-8")

    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())

    assert not (home / "history").exists()
    restored = load_agent_fallback(fallback)
    assert restored.model.model_name == "custom-model"


def test_init_runtime_does_not_overwrite_existing_host_config(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    host_config = home / "config.yaml"
    host_config.write_text("runtime:\n  default_core: evolver\nui:\n  user_message_align: right\n", encoding="utf-8")

    result = init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())

    assert result["host_config_created"] is False
    assert host_config.read_text(encoding="utf-8") == "runtime:\n  default_core: evolver\nui:\n  user_message_align: right\n"


def test_create_app_auto_initializes_missing_runtime_agent(tmp_path):
    home = tmp_path / "home"

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.version_store.active_core_path("assistant") == home / "agents" / "assistant"
    assert (home / "agents" / "agent.yaml").exists()
    assert (home / "agents" / "assistant" / "agent.yaml").exists()
    assert (home / "agents" / "evolver" / "agent.yaml").exists()
    assert app.version_store.active_pointer("assistant").reason == "live"


def test_create_app_only_fills_missing_runtime_defaults(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    fallback = home / "agents" / "agent.yaml"
    assistant_instructions = home / "agents" / "assistant" / "agent" / "SOUL.md"
    evolver_instructions = home / "agents" / "evolver" / "agent" / "SOUL.md"
    fallback.write_text("model:\n  model_name: user-fallback\n", encoding="utf-8")
    assistant_instructions.write_text("user assistant", encoding="utf-8")
    evolver_instructions.write_text("user evolver", encoding="utf-8")

    create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert fallback.read_text(encoding="utf-8") == "model:\n  model_name: user-fallback\n"
    assert assistant_instructions.read_text(encoding="utf-8") == "user assistant"
    assert evolver_instructions.read_text(encoding="utf-8") == "user evolver"


def test_create_app_rejects_agent_bound_fields_in_global_fallback(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    (home / "agents" / "agent.yaml").write_text("model: {}\nslots: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="only top-level 'model', 'ui', and 'approval' are supported"):
        create_app(home=home, provider_name="fake", agents_root=source_agents_root())


def test_create_app_allows_global_ui_fallback(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    (home / "agents" / "agent.yaml").write_text("model: {}\nui:\n  tool_display: full\n", encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.tool_display == "full"
    assert app.tool_display_source == "agents/agent.yaml:ui.tool_display"


def test_host_config_defaults_user_message_align_left_without_file(tmp_path):
    home = tmp_path / "home"

    config, sources = load_host_config(home / "config.yaml")
    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert config.runtime.default_core == "assistant"
    assert config.channel.busy_mode == "interrupt"
    assert config.ui.user_message_align == "left"
    assert config.ui.demiurge_theme_color == "#ff9afc"
    assert config.ui.user_theme_color == "#9cc9ff"
    assert config.debug.show_system_prompt is False
    assert sources == {}
    assert app.user_message_align == "left"
    assert app.user_message_align_source == "default"
    assert app.demiurge_theme_color == "#ff9afc"
    assert app.demiurge_theme_color_source == "default"
    assert app.user_theme_color == "#9cc9ff"
    assert app.user_theme_color_source == "default"
    assert app.debug_show_system_prompt is False
    assert app.debug_show_system_prompt_source == "default"
    assert app.runner.show_system_prompt is False
    assert app.workspace.root == (home / "workspace").resolve()
    assert (home / "workspace").is_dir()
    assert not (home / "config.yaml").exists()


def test_create_app_uses_server_local_timezone_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("DEMIURGE_TIMEZONE", raising=False)
    home = tmp_path / "home"

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())
    status = app.status()

    assert app.runtime_timezone.source == "server-local"
    assert app.runtime_timezone.explicit is False
    assert status["runtime_timezone"] == app.runtime_timezone.name
    assert status["runtime_timezone_source"] == "server-local"
    assert status["runtime_timezone_explicit"] is False
    assert "runtime_local_now" in status


def test_create_app_reads_host_config_runtime_timezone(monkeypatch, tmp_path):
    monkeypatch.delenv("DEMIURGE_TIMEZONE", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("runtime:\n  timezone: Asia/Shanghai\n", encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.runtime_timezone.name == "Asia/Shanghai"
    assert app.runtime_timezone.source == "config.yaml:runtime.timezone"
    assert app.runtime_timezone.explicit is True
    assert app.status()["runtime_timezone"] == "Asia/Shanghai"


def test_create_app_runtime_env_timezone_overrides_shell_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMIURGE_TIMEZONE", "America/New_York")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text('DEMIURGE_TIMEZONE="Europe/Paris"\n', encoding="utf-8")
    (home / "config.yaml").write_text("runtime:\n  timezone: UTC\n", encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.runtime_timezone.name == "Europe/Paris"
    assert app.runtime_timezone.source == "env:DEMIURGE_TIMEZONE"


def test_create_app_cli_timezone_overrides_env_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMIURGE_TIMEZONE", "Europe/Paris")
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("runtime:\n  timezone: UTC\n", encoding="utf-8")

    app = create_app(
        home=home,
        provider_name="fake",
        agents_root=source_agents_root(),
        timezone="Asia/Shanghai",
    )

    assert app.runtime_timezone.name == "Asia/Shanghai"
    assert app.runtime_timezone.source == "cli"


def test_create_app_rejects_invalid_env_timezone(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMIURGE_TIMEZONE", "Mars/Base")

    with pytest.raises(ValueError, match="unknown timezone: Mars/Base"):
        create_app(home=tmp_path / "home", provider_name="fake", agents_root=source_agents_root())


def test_create_app_rejects_invalid_cli_timezone(tmp_path):
    with pytest.raises(ValueError, match="unknown timezone: Mars/Base"):
        create_app(
            home=tmp_path / "home",
            provider_name="fake",
            agents_root=source_agents_root(),
            timezone="Mars/Base",
        )


def test_create_app_reads_host_config_user_message_align(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("ui:\n  user_message_align: right\n", encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.user_message_align == "right"
    assert app.user_message_align_source == "config.yaml:ui.user_message_align"
    assert app.status()["user_message_align"] == "right"
    assert app.status()["host_config"] == str(home / "config.yaml")


def test_create_app_reads_host_config_theme_colors(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("ui:\n  demiurge_theme_color: fac\n  user_theme_color: '#aabbcc'\n", encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.demiurge_theme_color == "#ffaacc"
    assert app.demiurge_theme_color_source == "config.yaml:ui.demiurge_theme_color"
    assert app.user_theme_color == "#aabbcc"
    assert app.user_theme_color_source == "config.yaml:ui.user_theme_color"
    assert app.status()["demiurge_theme_color"] == "#ffaacc"
    assert app.status()["user_theme_color"] == "#aabbcc"


def test_create_app_reads_host_config_debug_show_system_prompt(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("debug:\n  show_system_prompt: true\n", encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.debug_show_system_prompt is True
    assert app.debug_show_system_prompt_source == "config.yaml:debug.show_system_prompt"
    assert app.runner.show_system_prompt is True
    assert app.status()["debug_show_system_prompt"] is True
    assert app.status()["debug_show_system_prompt_source"] == "config.yaml:debug.show_system_prompt"


def test_create_app_uses_host_config_default_core_and_channel_busy_mode(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "runtime:\n  default_core: evolver\nchannel:\n  busy_mode: queue\n",
        encoding="utf-8",
    )

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.runner.core_id == "evolver"
    assert app.workspace.root == (home / "workspace").resolve()
    assert app.channel_busy_mode == "queue"
    assert app.channel_busy_mode_source == "config.yaml:channel.busy_mode"


def test_create_app_uses_core_manifest_workspace(tmp_path):
    home = tmp_path / "home"
    agents = tmp_path / "agents"
    shutil.copytree(source_agents_root(), agents)
    manifest_path = agents / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["workspace"] = "project"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=agents)

    assert app.workspace.root == (home / "agents" / "assistant" / "project").resolve()


def test_create_app_workspace_env_overrides_core_manifest(monkeypatch, tmp_path):
    home = tmp_path / "home"
    agents = tmp_path / "agents"
    env_workspace = tmp_path / "env-workspace"
    shutil.copytree(source_agents_root(), agents)
    manifest_path = agents / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["workspace"] = "project"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    env_workspace.mkdir()
    monkeypatch.setenv("DEMIURGE_WORKSPACE", str(env_workspace))

    app = create_app(home=home, provider_name="fake", agents_root=agents)

    assert app.workspace.root == env_workspace.resolve()


def test_create_app_workspace_fallback_overrides_core_manifest(tmp_path):
    home = tmp_path / "home"
    agents = tmp_path / "agents"
    fallback_workspace = tmp_path / "launch-workspace"
    shutil.copytree(source_agents_root(), agents)
    manifest_path = agents / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["workspace"] = "project"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    fallback_workspace.mkdir()

    app = create_app(home=home, provider_name="fake", agents_root=agents, workspace_fallback=fallback_workspace)

    assert app.workspace.root == fallback_workspace.resolve()


@pytest.mark.parametrize(
    ("content", "field"),
    [
        ("runtime:\n  default_core: ''\n", "runtime.default_core"),
        ("runtime:\n  timezone: Mars/Base\n", "runtime.timezone"),
        ("runtime:\n  workspace: ''\n", "runtime.workspace"),
        ("channel:\n  busy_mode: later\n", "channel.busy_mode"),
        ("ui:\n  user_message_align: center\n", "ui.user_message_align"),
        ("ui:\n  demiurge_theme_color: pink\n", "ui.demiurge_theme_color"),
        ("ui:\n  user_theme_color: '#12'\n", "ui.user_theme_color"),
        ("ui:\n  user_theme_color: '#gggggg'\n", "ui.user_theme_color"),
        ("debug:\n  show_system_prompt: 'yes'\n", "debug.show_system_prompt"),
    ],
)
def test_create_app_rejects_invalid_host_config_values(tmp_path, content, field):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        create_app(home=home, provider_name="fake", agents_root=source_agents_root())
    assert not (home / "agents").exists()


def test_default_host_config_writer_validates_existing_config(tmp_path):
    host_config = tmp_path / "home" / "config.yaml"
    host_config.parent.mkdir()
    host_config.write_text("channel:\n  busy_mode: later\n", encoding="utf-8")

    with pytest.raises(ValueError, match="channel.busy_mode"):
        write_default_host_config_if_missing(host_config)


def test_agent_fallback_schema_does_not_accept_user_message_align(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    (home / "agents" / "agent.yaml").write_text("model: {}\nui:\n  user_message_align: right\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ui.user_message_align"):
        create_app(home=home, provider_name="fake", agents_root=source_agents_root())


def test_create_app_resume_required_rejects_missing_session(tmp_path):
    with pytest.raises(FileNotFoundError, match="session not found"):
        create_app(
            home=tmp_path / "home",
            provider_name="fake",
            agents_root=source_agents_root(),
            session_id="missing-session",
            resume_required=True,
        )


def test_core_ui_config_overrides_global_ui_fallback(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    (home / "agents" / "agent.yaml").write_text("model: {}\nui:\n  tool_display: full\n", encoding="utf-8")
    manifest_path = home / "agents" / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["ui"] = {"tool_display": "quiet"}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    app = create_app(home=home, provider_name="fake", agents_root=source_agents_root())

    assert app.tool_display == "quiet"
    assert app.tool_display_source == "agent.yaml:ui.tool_display"


def test_source_agents_root_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom-agents"
    shutil.copytree(source_agents_root(), custom)
    monkeypatch.setenv("DEMIURGE_AGENTS_ROOT", str(custom))

    assert source_agents_root() == custom.resolve()


def test_cli_init_uses_agents_root_override(tmp_path, capsys):
    home = tmp_path / "home"
    custom = tmp_path / "custom-agents"
    shutil.copytree(source_agents_root(), custom)

    main(["--home", str(home), "--agents-root", str(custom), "init"])

    output = capsys.readouterr().out
    assert "initialized assistant@" in output
    assert (home / "agents" / "agent.yaml").exists()
    assert (home / "agents" / "assistant" / "agent.yaml").exists()
    assert (home / "agents" / "evolver" / "agent.yaml").exists()


def test_core_repository_uses_evolve_run_worktrees_and_git_refs(tmp_path):
    home = tmp_path / "home"
    store = VersionStore(home)
    store.init_from_source("assistant", source_agents_root() / "assistant")

    change_set = store.core_repository.begin_change_set(kind="evolve", reason="test", run_id="manual")
    candidate = change_set.agents_root / "assistant"
    assert candidate == home / ".evolve" / "runs" / "manual" / "agents" / "assistant"
    (candidate / "agent" / "SOUL.md").write_text("candidate", encoding="utf-8")

    proposal = change_set.commit_proposal(reason="test")
    promoted = store.core_repository.promote_run("manual", reason="test")

    assert promoted.revision == proposal.revision
    assert store.active_pointer("assistant").active_revision == promoted.revision
    assert (home / "agents" / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8") == "candidate"


def test_rollback_restores_history_snapshot(tmp_path):
    home = tmp_path / "home"
    store = VersionStore(home)
    store.init_from_source("assistant", source_agents_root() / "assistant")
    original = store.active_pointer("assistant").active_revision
    change_set = store.core_repository.begin_change_set(kind="evolve", reason="test", run_id="manual")
    candidate = change_set.agents_root / "assistant"
    (candidate / "agent" / "SOUL.md").write_text("candidate", encoding="utf-8")
    change_set.commit_proposal(reason="test")
    store.core_repository.promote_run("manual", reason="test")

    rolled_back = store.rollback("assistant", reason="test rollback")

    assert rolled_back.active_revision != original
    assert rolled_back.previous_revision is not None
    restored = home / "agents" / "assistant" / "agent" / "SOUL.md"
    assert "demiurge assistant" in restored.read_text(encoding="utf-8")


def test_init_runtime_rejects_missing_source_agent(tmp_path):
    with pytest.raises(FileNotFoundError):
        init_runtime(home=tmp_path / "home", core_id="missing", agents_root=tmp_path / "agents")
