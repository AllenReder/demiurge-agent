from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from demiurge.security.approval import ApprovalRuntime
from demiurge.core import AgentFallbackConfig, CoreLoader, ModelInfo, UiInfo
from demiurge.evolution import EvolutionRuntime
from demiurge.gates import GateRunner
from demiurge.mcp import McpRuntime
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.interactions import BridgeApprovalProvider
from demiurge.providers import FakeProvider, OpenAICompatibleProvider, Provider
from demiurge.storage import SessionStore, VersionStore
from demiurge.tools.runtime import ToolRuntime
from demiurge.util import default_home, ensure_dir
from demiurge.security.workspace import WorkspaceScope


class HostUiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    user_message_align: Literal["left", "right"] = "left"
    demiurge_theme_color: str = "ff9afc"
    user_theme_color: str = "9cc9ff"

    @field_validator("demiurge_theme_color", "user_theme_color", mode="before")
    @classmethod
    def _theme_color(cls, value: Any, info: Any) -> str:
        return normalize_hex_color(value, field_path=f"ui.{info.field_name}")


class HostRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_core: str = "assistant"

    @field_validator("default_core")
    @classmethod
    def _default_core_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("runtime.default_core must not be empty")
        return value.strip()


class HostChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    busy_mode: Literal["interrupt", "queue"] = "interrupt"


class HostDebugConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_system_prompt: StrictBool = False


class HostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: HostRuntimeConfig = Field(default_factory=HostRuntimeConfig)
    channel: HostChannelConfig = Field(default_factory=HostChannelConfig)
    ui: HostUiConfig = Field(default_factory=HostUiConfig)
    debug: HostDebugConfig = Field(default_factory=HostDebugConfig)


@dataclass(slots=True)
class DemiurgeApp:
    home: Path
    project_root: Path
    version_store: VersionStore
    core_loader: CoreLoader
    gate_runner: GateRunner
    evolution_runtime: EvolutionRuntime
    tool_runtime: ToolRuntime
    approval_runtime: ApprovalRuntime
    workspace: WorkspaceScope
    source_agents_root: Path
    runner: SessionTurnStepRunner
    provider_name: str
    model_name: str
    base_url: str | None
    base_url_override: str | None
    api_key_override_provided: bool
    tool_display: str
    tool_display_source: str
    channel_busy_mode: str
    channel_busy_mode_source: str
    user_message_align: str
    user_message_align_source: str
    demiurge_theme_color: str
    demiurge_theme_color_source: str
    user_theme_color: str
    user_theme_color_source: str
    debug_show_system_prompt: bool
    debug_show_system_prompt_source: str
    host_config_path: Path
    fallback_config_path: Path

    @property
    def agents_root(self) -> Path:
        return self.version_store.agents_root

    async def close(self) -> None:
        await self.tool_runtime.close()

    def status(self) -> dict[str, object]:
        pointer = self.version_store.active_pointer(self.runner.core_id)
        core = self.core_loader.load(self.version_store.active_core_path(self.runner.core_id))
        fallback = load_agent_fallback(self.version_store.fallback_config_path)
        model_name, model_source = resolve_model_name(core.manifest.model, fallback.model, override=self.runner.model_override)
        base_url, base_url_source = resolve_base_url(core.manifest.model, fallback.model, override=self.base_url_override)
        api_key_source = "cli" if self.api_key_override_provided else resolve_api_key(core.manifest.model, fallback.model)[1]
        tool_display, tool_display_source = resolve_tool_display(
            core.manifest.ui,
            fallback.ui,
            override=self.tool_display if self.tool_display_source == "cli" else None,
        )
        return {
            "home": str(self.home),
            "agents_root": str(self.version_store.agents_root),
            "host_config": str(self.host_config_path),
            "fallback_config": str(self.fallback_config_path),
            "source_agents_root": str(self.source_agents_root),
            "workspace": str(self.workspace.root),
            "provider": self.provider_name,
            "model": model_name,
            "model_source": model_source,
            "base_url": base_url,
            "base_url_source": base_url_source or "not configured",
            "api_key": api_key_source or "not configured",
            "tool_display": tool_display,
            "tool_display_source": tool_display_source,
            "channel_busy_mode": self.channel_busy_mode,
            "channel_busy_mode_source": self.channel_busy_mode_source,
            "user_message_align": self.user_message_align,
            "user_message_align_source": self.user_message_align_source,
            "demiurge_theme_color": self.demiurge_theme_color,
            "demiurge_theme_color_source": self.demiurge_theme_color_source,
            "user_theme_color": self.user_theme_color,
            "user_theme_color_source": self.user_theme_color_source,
            "debug_show_system_prompt": self.debug_show_system_prompt,
            "debug_show_system_prompt_source": self.debug_show_system_prompt_source,
            "approval_mode": self.approval_runtime.mode,
            "approval_cached_allows": self.approval_runtime.cached_allow_count,
            "session_id": self.runner.session_id,
            "session_store": str(self.home / "sessions" / self.runner.session_id),
            "session_messages": self.runner.session_store.message_count(self.runner.session_id),
            "has_compaction_summary": self.runner.session_store.latest_compaction_summary(self.runner.session_id)
            is not None,
            "core_id": pointer.core_id,
            "active_version": pointer.active_version,
            "previous_stable_version": pointer.previous_stable_version,
            "versions": self.version_store.list_versions(pointer.core_id),
        }


def create_app(
    *,
    home: Path | None = None,
    project_root: Path | None = None,
    core_id: str | None = None,
    provider_name: str = "auto",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    fake_script: Path | None = None,
    workspace: Path | None = None,
    workspace_fallback: Path | None = None,
    agents_root: Path | None = None,
    tool_display: str | None = None,
    session_id: str | None = None,
    resume_required: bool = False,
) -> DemiurgeApp:
    project_root = project_root or Path.cwd().resolve()
    home = ensure_dir((home or default_home()).resolve())
    host_config_path = home / "config.yaml"
    host_config, host_sources = load_host_config(host_config_path)
    resolved_core_id = core_id or host_config.runtime.default_core or "assistant"
    source_agents = source_agents_root(agents_root)
    approval_runtime = ApprovalRuntime(BridgeApprovalProvider())
    version_store = VersionStore(home)
    ensure_runtime_defaults(version_store, source_agents, requested_core_id=resolved_core_id)
    if resume_required and session_id and not SessionStore(home).exists(session_id):
        raise FileNotFoundError(f"session not found: {session_id}")

    core_loader = CoreLoader()
    active_core = core_loader.load(version_store.active_core_path(resolved_core_id))
    workspace_root = _resolve_workspace(
        workspace=workspace,
        workspace_fallback=workspace_fallback,
        core_root=active_core.root,
        configured_workspace=active_core.manifest.runtime.workspace,
        home=home,
    )
    workspace_scope = WorkspaceScope(workspace_root)
    fallback = load_agent_fallback(version_store.fallback_config_path)
    resolved_model, _ = resolve_model_name(active_core.manifest.model, fallback.model, override=model)
    resolved_base_url, _ = resolve_base_url(active_core.manifest.model, fallback.model, override=base_url)
    resolved_api_key, _ = resolve_api_key(active_core.manifest.model, fallback.model, override=api_key)
    resolved_tool_display, tool_display_source = resolve_tool_display(active_core.manifest.ui, fallback.ui, override=tool_display)
    provider, resolved_provider_name = create_provider(
        provider_name=provider_name,
        model_info=active_core.manifest.model,
        fallback_model_info=fallback.model,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        fake_script=fake_script,
    )
    gate_runner = GateRunner(project_root=project_root)
    mcp_runtime = McpRuntime(home=home, workspace=workspace_scope.root)
    tool_runtime = ToolRuntime(
        version_store,
        workspace=workspace_scope,
        approval_runtime=approval_runtime,
        global_approval=fallback.approval,
        mcp_runtime=mcp_runtime,
    )
    evolution_runtime = EvolutionRuntime(version_store=version_store, gate_runner=gate_runner)
    tool_runtime.evolution_runtime = evolution_runtime
    runner = SessionTurnStepRunner(
        home=home,
        version_store=version_store,
        core_loader=core_loader,
        provider=provider,
        tool_runtime=tool_runtime,
        core_id=resolved_core_id,
        session_id=session_id,
        model_override=model,
        model_resolver=lambda core_model: resolve_model_name(core_model, fallback.model, override=model)[0],
        provider_name=resolved_provider_name,
        workspace=str(workspace_scope.root),
        show_system_prompt=host_config.debug.show_system_prompt,
    )
    return DemiurgeApp(
        home=home,
        project_root=project_root,
        version_store=version_store,
        core_loader=core_loader,
        gate_runner=gate_runner,
        evolution_runtime=evolution_runtime,
        tool_runtime=tool_runtime,
        approval_runtime=approval_runtime,
        workspace=workspace_scope,
        source_agents_root=source_agents,
        runner=runner,
        provider_name=resolved_provider_name,
        model_name=resolved_model,
        base_url=resolved_base_url,
        base_url_override=base_url,
        api_key_override_provided=api_key is not None,
        tool_display=resolved_tool_display,
        tool_display_source=tool_display_source,
        channel_busy_mode=host_config.channel.busy_mode,
        channel_busy_mode_source=host_sources.get("channel.busy_mode", "default"),
        user_message_align=host_config.ui.user_message_align,
        user_message_align_source=host_sources.get("ui.user_message_align", "default"),
        demiurge_theme_color=host_config.ui.demiurge_theme_color,
        demiurge_theme_color_source=host_sources.get("ui.demiurge_theme_color", "default"),
        user_theme_color=host_config.ui.user_theme_color,
        user_theme_color_source=host_sources.get("ui.user_theme_color", "default"),
        debug_show_system_prompt=host_config.debug.show_system_prompt,
        debug_show_system_prompt_source=host_sources.get("debug.show_system_prompt", "default"),
        host_config_path=host_config_path,
        fallback_config_path=version_store.fallback_config_path,
    )


def init_runtime(
    *,
    home: Path | None = None,
    core_id: str = "assistant",
    agents_root: Path | None = None,
    reason: str = "init",
) -> dict[str, object]:
    resolved_home = ensure_dir((home or default_home()).resolve())
    source_agents = source_agents_root(agents_root)
    host_config_path = resolved_home / "config.yaml"
    host_config_created = write_default_host_config_if_missing(host_config_path)
    version_store = VersionStore(resolved_home)
    version_store.init_fallback_from_source(source_agents / "agent.yaml", reason=reason, overwrite=True)
    pointers = {
        item: version_store.init_from_source(item, source_agents / item, reason=reason)
        for item in dict.fromkeys([core_id, "evolver"])
    }
    pointer = pointers[core_id]
    evolver_pointer = pointers["evolver"]
    return {
        "home": str(resolved_home),
        "host_config": str(host_config_path),
        "host_config_created": host_config_created,
        "agents_root": str(source_agents),
        "fallback_config": str(version_store.fallback_config_path),
        "active_path": str(version_store.active_core_path(core_id)),
        "core_id": pointer.core_id,
        "active_version": pointer.active_version,
        "previous_stable_version": pointer.previous_stable_version,
        "evolver_active_path": str(version_store.active_core_path("evolver")),
        "evolver_version": evolver_pointer.active_version,
    }


def refresh_runtime(
    *,
    home: Path | None = None,
    target: str = "all",
    core_id: str = "assistant",
    agents_root: Path | None = None,
    reason: str = "refresh",
) -> dict[str, object]:
    resolved_home = ensure_dir((home or default_home()).resolve())
    source_agents = source_agents_root(agents_root)
    version_store = VersionStore(resolved_home)
    refreshed: dict[str, object] = {
        "home": str(resolved_home),
        "source_agents_root": str(source_agents),
        "target": target,
        "items": {},
    }
    targets: list[str]
    if target == "all":
        targets = ["global", "assistant", "evolver", core_id]
    elif target == "global":
        targets = ["global"]
    else:
        targets = [target]
    items: dict[str, object] = {}
    for item in dict.fromkeys(targets):
        if item == "global":
            prior = version_store.init_fallback_from_source(source_agents / "agent.yaml", reason=reason, overwrite=True)
            items[item] = {
                "path": str(version_store.fallback_config_path),
                "previous": prior,
            }
            continue
        pointer = version_store.init_from_source(item, source_agents / item, reason=reason)
        items[item] = {
            "path": str(version_store.active_core_path(item)),
            "active_version": pointer.active_version,
            "previous_stable_version": pointer.previous_stable_version,
        }
    refreshed["items"] = items
    return refreshed


def source_agents_root(override: Path | None = None) -> Path:
    if override:
        return override.expanduser().resolve()
    env = os.environ.get("DEMIURGE_AGENTS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    checkout_agents = Path(__file__).resolve().parents[2] / "agents"
    if checkout_agents.exists():
        return checkout_agents
    return Path(str(files("demiurge.resources").joinpath("agents")))


def ensure_runtime_defaults(version_store: VersionStore, source_agents: Path, *, requested_core_id: str) -> None:
    version_store.ensure_fallback_initialized(source_agents / "agent.yaml")
    for core_id in dict.fromkeys(["assistant", "evolver", requested_core_id]):
        version_store.ensure_initialized(core_id, source_agents / core_id)


def load_agent_fallback(path: Path) -> AgentFallbackConfig:
    if not path.exists():
        return AgentFallbackConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AgentFallbackConfig.model_validate(raw)
    except (ValidationError, yaml.YAMLError) as exc:
        raise ValueError(
            f"invalid global fallback agent config {path}: only top-level 'model', 'ui', and 'approval' are supported: {exc}"
        ) from exc


def load_host_config(path: Path) -> tuple[HostConfig, dict[str, str]]:
    if not path.exists():
        return HostConfig(), {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("expected a mapping")
        config = HostConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"invalid host config {path}: supported fields are runtime.default_core, "
            f"channel.busy_mode, ui.user_message_align, ui.demiurge_theme_color, "
            f"ui.user_theme_color, and debug.show_system_prompt: {exc}"
        ) from exc
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(
            f"invalid host config {path}: supported fields are runtime.default_core, "
            f"channel.busy_mode, ui.user_message_align, ui.demiurge_theme_color, "
            f"ui.user_theme_color, and debug.show_system_prompt: {exc}"
        ) from exc
    return config, _host_config_sources(raw)


def default_host_config_dict() -> dict[str, object]:
    return {
        "runtime": {
            "default_core": "assistant",
        },
        "channel": {
            "busy_mode": "interrupt",
        },
        "ui": {
            "user_message_align": "left",
            "demiurge_theme_color": "ff9afc",
            "user_theme_color": "9cc9ff",
        },
        "debug": {
            "show_system_prompt": False,
        },
    }


def write_default_host_config_if_missing(path: Path) -> bool:
    if path.exists():
        load_host_config(path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(default_host_config_dict(), sort_keys=False), encoding="utf-8")
    return True


def _host_config_sources(raw: dict[str, Any]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for section, key in (
        ("runtime", "default_core"),
        ("channel", "busy_mode"),
        ("ui", "user_message_align"),
        ("ui", "demiurge_theme_color"),
        ("ui", "user_theme_color"),
        ("debug", "show_system_prompt"),
    ):
        raw_section = raw.get(section)
        if isinstance(raw_section, dict) and key in raw_section:
            sources[f"{section}.{key}"] = f"config.yaml:{section}.{key}"
    return sources


def normalize_hex_color(value: Any, *, field_path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a hex color")
    raw = value.strip().lower()
    if raw.startswith("#"):
        raw = raw[1:]
    if re.fullmatch(r"[0-9a-f]{3}", raw):
        raw = "".join(ch * 2 for ch in raw)
    if not re.fullmatch(r"[0-9a-f]{6}", raw):
        raise ValueError(f"{field_path} must be a 3- or 6-digit hex color")
    return f"#{raw}"


def _resolve_workspace(
    *,
    workspace: Path | None,
    workspace_fallback: Path | None,
    core_root: Path,
    configured_workspace: str | None,
    home: Path,
) -> Path:
    if workspace is not None:
        return workspace.expanduser().resolve()
    value = os.environ.get("DEMIURGE_WORKSPACE")
    if value:
        return Path(value).expanduser().resolve()
    if workspace_fallback is not None:
        return workspace_fallback.expanduser().resolve()
    if configured_workspace is not None:
        path = Path(configured_workspace).expanduser()
        if not path.is_absolute():
            path = core_root / path
        return path.resolve()
    return ensure_dir(home / "workspace")


def create_provider(
    *,
    provider_name: str,
    model_info: ModelInfo | None = None,
    fallback_model_info: ModelInfo | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    fake_script: Path | None = None,
) -> tuple[Provider, str]:
    configured_provider = (
        (model_info.provider if model_info else None)
        or (fallback_model_info.provider if fallback_model_info else None)
        or "auto"
    )
    configured_base_url = base_url or resolve_base_url(model_info, fallback_model_info)[0]
    configured_api_key, _ = resolve_api_key(model_info, fallback_model_info, override=api_key)
    if provider_name == "auto":
        provider_name = configured_provider
    if provider_name == "auto":
        provider_name = "openai" if configured_api_key else "fake"
    if provider_name == "fake":
        return FakeProvider(fake_script), "fake"
    if provider_name in {"openai", "openai-compatible"}:
        return OpenAICompatibleProvider(api_key=configured_api_key, base_url=configured_base_url), "openai"
    raise ValueError(f"unknown provider: {provider_name}")


def resolve_model_name(
    model_info: ModelInfo | None,
    fallback_model_info: ModelInfo | None = None,
    *,
    override: str | None = None,
) -> tuple[str, str]:
    if override:
        return override, "cli"
    for item, prefix in (
        (model_info, "agent.yaml:model"),
        (fallback_model_info, "agents/agent.yaml:model"),
    ):
        value, source = _value_from_model_info(
            item,
            env_attr="model_name_env",
            direct_attr="model_name",
            prefix=prefix,
        )
        if value:
            return value, source
    value = os.environ.get("DEMIURGE_MODEL_NAME")
    if value:
        return value, "env:DEMIURGE_MODEL_NAME"
    return "fake/demo", "default"


def resolve_base_url(
    model_info: ModelInfo | None,
    fallback_model_info: ModelInfo | None = None,
    *,
    override: str | None = None,
) -> tuple[str | None, str | None]:
    if override:
        return override, "cli"
    return _resolve_model_value(
        model_info,
        fallback_model_info,
        env_attr="base_url_env",
        direct_attr="base_url",
        standard_env="OPENAI_BASE_URL",
        default_value=None,
    )


def resolve_api_key(
    model_info: ModelInfo | None,
    fallback_model_info: ModelInfo | None = None,
    *,
    override: str | None = None,
) -> tuple[str | None, str | None]:
    if override:
        return override, "cli"
    return _resolve_model_value(
        model_info,
        fallback_model_info,
        env_attr="api_key_env",
        direct_attr="api_key",
        standard_env="OPENAI_API_KEY",
        default_value=None,
    )


def resolve_model_options(model_info: ModelInfo | None, fallback_model_info: ModelInfo | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if fallback_model_info:
        merged.update(fallback_model_info.model_options)
    if model_info:
        merged.update(model_info.model_options)
    return merged


def resolve_tool_display(
    ui_info: UiInfo | None,
    fallback_ui_info: UiInfo | None = None,
    *,
    override: str | None = None,
) -> tuple[str, str]:
    if override:
        return _normalize_tool_display(override, source="cli"), "cli"
    if ui_info and ui_info.tool_display:
        return _normalize_tool_display(ui_info.tool_display, source="agent.yaml:ui.tool_display"), "agent.yaml:ui.tool_display"
    if fallback_ui_info and fallback_ui_info.tool_display:
        return (
            _normalize_tool_display(fallback_ui_info.tool_display, source="agents/agent.yaml:ui.tool_display"),
            "agents/agent.yaml:ui.tool_display",
        )
    return "summary", "default"


def _normalize_tool_display(value: str, *, source: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"quiet", "summary", "full"}:
        raise ValueError(f"invalid tool_display in {source}: expected quiet, summary, or full")
    return normalized


def _resolve_model_value(
    model_info: ModelInfo | None,
    fallback_model_info: ModelInfo | None,
    *,
    env_attr: str | None,
    direct_attr: str,
    standard_env: str | None,
    default_value: str | None,
) -> tuple[str | None, str | None]:
    value, source = _value_from_model_info(model_info, env_attr=env_attr, direct_attr=direct_attr, prefix="agent.yaml:model")
    if value:
        return value, source
    value, source = _value_from_model_info(
        fallback_model_info,
        env_attr=env_attr,
        direct_attr=direct_attr,
        prefix="agents/agent.yaml:model",
    )
    if value:
        return value, source
    if standard_env:
        value = os.environ.get(standard_env)
        if value:
            return value, f"env:{standard_env}"
    if default_value is not None:
        return default_value, "default"
    return None, None


def _value_from_model_info(
    model_info: ModelInfo | None,
    *,
    env_attr: str | None,
    direct_attr: str,
    prefix: str,
) -> tuple[str | None, str | None]:
    if model_info is None:
        return None, None
    if env_attr:
        env_name = getattr(model_info, env_attr)
        value = _env_value(env_name)
        if value:
            return value, f"env:{env_name}"
    value = getattr(model_info, direct_attr)
    if value:
        return value, f"{prefix}.{direct_attr}"
    return None, None


def _env_value(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value
