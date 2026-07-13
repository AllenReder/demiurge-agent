from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, Mapping
from weakref import WeakValueDictionary

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

from demiurge.env_file import load_runtime_env
from demiurge.security.approval import ApprovalRuntime
from demiurge.core import AgentFallbackConfig, ApprovalInfo, CoreLoader, ModelInfo, UiInfo
from demiurge.evolution import EvolutionRuntime, EvolverRunResult, PROTECTED_DEPENDENCY_FILES
from demiurge.gates import GateRunner
from demiurge.runtime.tasks import RuntimeTaskWorker
from demiurge.runtime.host_work import HostWorkLifecycleRuntime
from demiurge.mcp import McpRuntime
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.interactions import BridgeApprovalProvider, SessionInteractionRouter
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.scope import (
    PrincipalScopeResolver,
    _activate_operator_authority,
    _deactivate_operator_authority,
)
from demiurge.runtime.store import RuntimeStore
from demiurge.providers import Provider, ProviderFactoryConfig, create_provider_from_config
from demiurge.providers.profiles import (
    ProviderRuntimeProfile,
    get_builtin_provider_profile,
    is_builtin_provider,
)
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone, validate_timezone_name
from demiurge.storage import VersionStore
from demiurge.tools.runtime import ToolRuntime
from demiurge.util import default_home, ensure_dir, utc_id
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
    timezone: str | None = None

    @field_validator("default_core")
    @classmethod
    def _default_core_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("runtime.default_core must not be empty")
        return value.strip()

    @field_validator("timezone", mode="before")
    @classmethod
    def _timezone(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("runtime.timezone must be an IANA timezone string")
        normalized = value.strip()
        if not normalized:
            return None
        return validate_timezone_name(normalized)


class HostChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    busy_mode: Literal["interrupt", "queue"] = "interrupt"


class HostDebugConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_system_prompt: StrictBool = False


class HostBuiltinProviderOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    base_url: str | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def _optional_text(cls, value: Any, info: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"providers builtin override {info.field_name} must be a string")
        normalized = value.strip()
        return normalized or None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"https?://\S+", value):
            raise ValueError("providers builtin override base_url must be an http(s) URL")
        return value.rstrip("/")


class HostCustomProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    api_mode: Literal["openai-chat", "anthropic-messages"] = "openai-chat"
    base_url: str
    api_key_env: str | None = None
    api_key: str | None = None

    @field_validator("api_mode", mode="before")
    @classmethod
    def _api_mode(cls, value: Any) -> Any:
        if value is None:
            return "openai-chat"
        if not isinstance(value, str):
            raise ValueError("providers custom profile api_mode must be a string")
        return value.strip()

    @field_validator("base_url", "api_key_env", "api_key", mode="before")
    @classmethod
    def _optional_text(cls, value: Any, info: Any) -> Any:
        if value is None:
            return None if info.field_name != "base_url" else value
        if not isinstance(value, str):
            raise ValueError(f"providers custom profile {info.field_name} must be a string")
        normalized = value.strip()
        if not normalized:
            return None if info.field_name != "base_url" else normalized
        return normalized

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        if not value:
            raise ValueError("providers custom profile base_url must not be empty")
        if not re.fullmatch(r"https?://\S+", value):
            raise ValueError("providers custom profile base_url must be an http(s) URL")
        return value.rstrip("/")


class HostProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    default: str | None = None
    builtin: dict[str, HostBuiltinProviderOverride] = Field(default_factory=dict)
    custom: dict[str, HostCustomProviderProfile] = Field(default_factory=dict)

    @field_validator("default", mode="before")
    @classmethod
    def _default(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("providers.default must be a string")
        normalized = value.strip()
        return normalized or None

    @field_validator("builtin")
    @classmethod
    def _builtin(cls, value: dict[str, HostBuiltinProviderOverride]) -> dict[str, HostBuiltinProviderOverride]:
        for provider_id in value:
            _validate_provider_id(provider_id)
            if not is_builtin_provider(provider_id):
                raise ValueError(f"unknown builtin provider id: {provider_id}")
        return value

    @field_validator("custom")
    @classmethod
    def _custom(cls, value: dict[str, HostCustomProviderProfile]) -> dict[str, HostCustomProviderProfile]:
        for profile_id in value:
            _validate_provider_id(profile_id)
            if profile_id == "fake" or is_builtin_provider(profile_id):
                raise ValueError(f"custom provider id cannot shadow builtin provider: {profile_id}")
        return value


class HostPackageRepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    type: Literal["builtin", "path", "git"]
    path: str | None = None
    url: str | None = None
    ref: str | None = None
    subdir: str | None = None
    trusted: StrictBool = False

    @field_validator("path", "url", "ref", "subdir", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("package repository fields must be strings")
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _shape(self) -> "HostPackageRepositoryConfig":
        if self.type == "builtin":
            if self.path or self.url or self.ref or self.subdir:
                raise ValueError("builtin package repository cannot set path, url, ref, or subdir")
        elif self.type == "path":
            if not self.path:
                raise ValueError("path package repository requires path")
            if self.url:
                raise ValueError("path package repository cannot set url")
        elif self.type == "git":
            if not self.url:
                raise ValueError("git package repository requires url")
            if self.path:
                raise ValueError("git package repository cannot set path")
        return self


class HostPackagesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    repositories: dict[str, HostPackageRepositoryConfig] = Field(
        default_factory=lambda: {"builtin": HostPackageRepositoryConfig(type="builtin")}
    )

    @field_validator("repositories")
    @classmethod
    def _repositories(cls, value: dict[str, HostPackageRepositoryConfig]) -> dict[str, HostPackageRepositoryConfig]:
        if "builtin" not in value:
            value = {"builtin": HostPackageRepositoryConfig(type="builtin"), **value}
        for alias in value:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias):
                raise ValueError(f"invalid package repository alias: {alias}")
        return value


class HostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: HostRuntimeConfig = Field(default_factory=HostRuntimeConfig)
    channel: HostChannelConfig = Field(default_factory=HostChannelConfig)
    ui: HostUiConfig = Field(default_factory=HostUiConfig)
    debug: HostDebugConfig = Field(default_factory=HostDebugConfig)
    providers: HostProvidersConfig = Field(default_factory=HostProvidersConfig)
    packages: HostPackagesConfig = Field(default_factory=HostPackagesConfig)


@dataclass(frozen=True, slots=True)
class ResolvedHostProviderProfile:
    provider_id: str
    profile_kind: Literal["builtin", "custom"]
    profile_source: str
    runtime_profile: ProviderRuntimeProfile
    api_mode: str
    api_mode_source: str
    base_url: str
    base_url_source: str
    api_key_env: str | None
    api_key_env_source: str | None
    api_key: str | None
    api_key_source: str | None


@dataclass(frozen=True, slots=True)
class ResolvedProviderConfig:
    provider_id: str
    provider_source: str
    api_mode: str
    api_mode_source: str | None
    base_url: str | None
    base_url_source: str | None
    api_key: str | None
    api_key_source: str | None
    runtime_profile: ProviderRuntimeProfile | None = None


_ACTIVE_APP_LIFECYCLES: WeakValueDictionary[int, Any] = WeakValueDictionary()


def _is_active_app_lifecycle(host: Any) -> bool:
    return _ACTIVE_APP_LIFECYCLES.get(id(host)) is host and not host._closed


@dataclass(slots=True, weakref_slot=True)
class DemiurgeApp:
    home: Path
    project_root: Path
    version_store: VersionStore
    core_loader: CoreLoader
    gate_runner: GateRunner
    evolution_runtime: EvolutionRuntime
    runtime_store: RuntimeStore
    control_plane: RuntimeControlPlane
    host_work: HostWorkLifecycleRuntime
    session_runtime: SessionRuntime
    interaction_router: SessionInteractionRouter
    task_worker: RuntimeTaskWorker
    tool_runtime: ToolRuntime
    approval_runtime: ApprovalRuntime
    workspace: WorkspaceScope
    source_agents_root: Path
    runner: SessionTurnStepRunner
    provider_name: str
    provider_source: str
    provider_api_mode: str
    model_name: str
    model_name_source: str
    base_url: str | None
    base_url_source: str | None
    api_key_source: str | None
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
    runtime_timezone: RuntimeTimezone
    runtime_recovery_summary: dict[str, int]
    host_config_path: Path
    fallback_config_path: Path
    _operator_authority: object | None
    _closed: bool

    @property
    def agents_root(self) -> Path:
        return self.version_store.agents_root

    async def prepare_live_core(self) -> None:
        await self.version_store.core_repository.prepare_live_for_edit_async(
            validate=lambda agents_root, changed_paths: self.gate_runner.run(agents_root, changed_paths=changed_paths)
        )

    async def load_active_core(self):
        await self.prepare_live_core()
        return self.core_loader.load(self.version_store.active_core_path(self.runner.core_id))

    async def close(self) -> None:
        if self._closed:
            return
        try:
            try:
                await self.task_worker.shutdown()
            finally:
                await self.tool_runtime.close()
        finally:
            self.approval_runtime.close()
            _deactivate_operator_authority(
                self.runtime_store,
                self,
                self._operator_authority,
            )
            self._operator_authority = None
            self._closed = True
            _ACTIVE_APP_LIFECYCLES.pop(id(self), None)

    def status(self) -> dict[str, object]:
        pointer = self.version_store.active_pointer(self.runner.core_id)
        core = self.core_loader.load(self.version_store.active_core_path(self.runner.core_id))
        fallback = load_agent_fallback(self.version_store.fallback_config_path)
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
            "provider_source": self.provider_source,
            "provider_api_mode": self.provider_api_mode,
            "model": self.model_name,
            "model_source": self.model_name_source,
            "base_url": self.base_url,
            "base_url_source": self.base_url_source or "not configured",
            "api_key": self.api_key_source or "not configured",
            "tool_display": tool_display,
            "tool_display_source": tool_display_source,
            "runtime_timezone": self.runtime_timezone.name,
            "runtime_timezone_source": self.runtime_timezone.source,
            "runtime_timezone_explicit": self.runtime_timezone.explicit,
            "runtime_local_now": self.runtime_timezone.local_now().isoformat(),
            "runtime_store": str(self.runtime_store.path),
            "runtime_recovery": dict(self.runtime_recovery_summary),
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
            "background_tasks": self.runner.background_tasks.active_count,
            "session_id": self.runner.session_id,
            "session_messages": self.session_runtime.message_count(self.runner.session_id),
            "has_compaction_summary": self.session_runtime.latest_compaction_summary(self.runner.session_id)
            is not None,
            "core_id": pointer.core_id,
            "active_revision": pointer.active_revision,
            "previous_revision": pointer.previous_revision,
            "revisions": self.version_store.list_versions(pointer.core_id),
        }


class HostEvolverRunner:
    def __init__(
        self,
        *,
        home: Path,
        project_root: Path,
        version_store: VersionStore,
        core_loader: CoreLoader,
        host_config: HostConfig,
        fallback: AgentFallbackConfig,
        runtime_timezone: RuntimeTimezone,
        api_key_override: str | None,
        session_runtime: SessionRuntime,
        task_worker: RuntimeTaskWorker,
    ):
        self.home = home
        self.project_root = project_root
        self.version_store = version_store
        self.core_loader = core_loader
        self.host_config = host_config
        self.fallback = fallback
        self.runtime_timezone = runtime_timezone
        self.api_key_override = api_key_override
        self.session_runtime = session_runtime
        self.task_worker = task_worker

    async def run(
        self,
        *,
        run_id: str,
        goal: str,
        target_core_id: str,
        agents_root: Path,
        target_core_path: Path,
        reference_agents_root: Path,
        run_root: Path,
    ) -> EvolverRunResult:
        evolver_core_path = self.version_store.active_core_path("evolver")
        evolver_core = self.core_loader.load(evolver_core_path)
        provider_config = resolve_provider_config(
            self.host_config,
            evolver_core.manifest.model,
            self.fallback.model,
            api_key_override=self.api_key_override,
        )
        provider, provider_name = create_provider(
            provider_config=provider_config,
            fake_script=None,
        )
        workspace = WorkspaceScope(
            agents_root,
            write_root=agents_root,
            read_roots=[
                reference_agents_root,
                self.project_root / "README.md",
                self.project_root / "docs",
            ],
            blocked_write_names=PROTECTED_DEPENDENCY_FILES,
        )
        tool_runtime = ToolRuntime(
            self.version_store,
            workspace=workspace,
            approval_runtime=ApprovalRuntime(),
            global_approval=ApprovalInfo(default="auto"),
            runtime_timezone=self.runtime_timezone,
            task_worker=self.task_worker,
            session_runtime=self.session_runtime,
        )
        runner = SessionTurnStepRunner(
            home=self.home,
            version_store=self.version_store,
            core_loader=self.core_loader,
            provider=provider,
            tool_runtime=tool_runtime,
            core_id="evolver",
            provider_name=provider_name,
            workspace=str(agents_root),
            initial_core_path=evolver_core_path,
            model_resolver=lambda core_model: resolve_model_name(core_model, self.fallback.model)[0],
            runtime_timezone=self.runtime_timezone,
            task_worker=self.task_worker,
            session_runtime=self.session_runtime,
        )
        try:
            result = await runner.run_turn(
                self._prompt(
                    run_id=run_id,
                    goal=goal,
                    target_core_id=target_core_id,
                    agents_root=agents_root,
                    target_core_path=target_core_path,
                    reference_agents_root=reference_agents_root,
                    run_root=run_root,
                ),
                core_path=evolver_core_path,
            )
        finally:
            await tool_runtime.close()
        summary = "\n".join(delivery.text for delivery in result.deliveries if delivery.text).strip()
        if not summary:
            summary = result.agent_result if isinstance(result.agent_result, str) else ""
        return EvolverRunResult(
            summary=summary,
            session_id=result.session_id,
            turn_id=result.turn_id,
            needs_user=_turn_result_needs_user(result),
        )

    def _prompt(
        self,
        *,
        run_id: str,
        goal: str,
        target_core_id: str,
        agents_root: Path,
        target_core_path: Path,
        reference_agents_root: Path,
        run_root: Path,
    ) -> str:
        docs_path = self.project_root / "docs"
        readme_path = self.project_root / "README.md"
        return "\n".join(
            [
                f"Evolution run: {run_id}",
                f"Target core: {target_core_id}",
                "",
                "Goal:",
                goal.strip() or "Make the requested agent-core improvement.",
                "",
                "Editable Agent Core tree:",
                str(agents_root),
                "",
                "Target core path:",
                str(target_core_path),
                "",
                "Read-only reference paths:",
                f"- Previous live agents tree: {reference_agents_root}",
                f"- README: {readme_path}",
                f"- Docs: {docs_path}",
                "",
                "Before editing, inspect:",
                f"- {target_core_path / 'agent.yaml'}",
                f"- {target_core_path / 'agent' / 'pipelines.yaml'}",
                "- relevant existing directories under agent/bootstrap, agent/input, agent/output, agent/tools, agent/skills, and agent/lib",
                f"- {docs_path / 'how-to/write-slot-module.md'}",
                f"- {docs_path / 'reference/slot-context-sdk.md'}",
                f"- {docs_path / 'reference/contracts/slot-modules.md'}",
                f"- {docs_path / 'reference/slots-yaml.md'}",
                f"- {docs_path / 'reference/contracts/evolver-safe-edits.md'}",
                "",
                "Classify the requested change before editing:",
                "- Pre-model context, user-input normalization, memory recall, attachment interpretation, or skill activation -> input slot",
                "- Post-model delivery, TTS, notifications, archiving, channel rendering, or structured extraction -> output slot",
                "- Session-stable context initialized once -> bootstrap slot",
                "- Model-callable action -> authored tool",
                "- Reusable instruction, procedure, or knowledge -> skill",
                "- Shared Python helper code -> agent/lib",
                "",
                "Default edit policy:",
                "- Prefer adding a new named slot, tool, skill, or helper over modifying seed files.",
                "- Do not modify base_input or base_output unless the goal explicitly asks to replace seed behavior or the seed slot itself is broken.",
                "- When adding a slot, create module.py and slot.yaml together, then insert the slot id into the existing agent/pipelines.yaml list.",
                "- Preserve unrelated pipeline phases, existing slots, and seed slots.",
                "",
                "Edit only files inside the editable Agent Core tree. You may update the target core and helper cores when needed.",
                "Do not commit, promote, roll back, edit host config, sessions, state, source checkout files, .temp, or dependency files.",
                "Use terminal only with cwd inside the editable agents tree.",
                f"The host writes reports under this run directory; do not edit it: {run_root}",
                "When finished, respond with changed behavior, files edited, verification performed, and limitations or follow-up.",
            ]
        )


def _turn_result_needs_user(result: Any) -> bool:
    if bool(getattr(result, "needs_user", False)):
        return True
    for record in getattr(result, "tool_results", []):
        data = getattr(getattr(record, "result", None), "data", None)
        if not isinstance(data, Mapping):
            continue
        if data.get("needs_user"):
            return True
        approval = data.get("approval")
        if isinstance(approval, Mapping):
            reason = str(approval.get("reason") or "").lower()
            if approval.get("value") == "deny" and "no_interactive_route" in reason:
                return True
    return False


def create_app(
    *,
    home: Path | None = None,
    project_root: Path | None = None,
    core_id: str | None = None,
    provider_name: str = "auto",
    model: str | None = None,
    api_key: str | None = None,
    fake_script: Path | None = None,
    workspace: Path | None = None,
    workspace_fallback: Path | None = None,
    agents_root: Path | None = None,
    tool_display: str | None = None,
    timezone: str | None = None,
    session_id: str | None = None,
    resume_required: bool = False,
) -> DemiurgeApp:
    project_root = project_root or Path.cwd().resolve()
    home = ensure_dir((home or default_home()).resolve())
    load_runtime_env(home)
    host_config_path = home / "config.yaml"
    host_config, host_sources = load_host_config(host_config_path)
    runtime_timezone = resolve_runtime_timezone(
        override=timezone,
        config_value=host_config.runtime.timezone,
        config_source=host_sources.get("runtime.timezone", "config.yaml:runtime.timezone"),
    )
    resolved_core_id = core_id or host_config.runtime.default_core or "assistant"
    source_agents = source_agents_root(agents_root)
    interaction_router = SessionInteractionRouter()
    approval_runtime = ApprovalRuntime(BridgeApprovalProvider(interaction_router))
    version_store = VersionStore(
        home,
        on_core_changed=approval_runtime.invalidate_core,
    )
    ensure_runtime_defaults(version_store, source_agents, requested_core_id=resolved_core_id)
    runtime_store = RuntimeStore.default(home)
    control_plane = RuntimeControlPlane(runtime_store)
    host_work = HostWorkLifecycleRuntime(store=runtime_store)
    session_runtime = SessionRuntime(control_plane=control_plane)
    if resume_required and session_id and not session_runtime.exists(session_id):
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
    resolved_model, resolved_model_source = resolve_model_name(active_core.manifest.model, fallback.model, override=model)
    provider_config = resolve_provider_config(
        host_config,
        active_core.manifest.model,
        fallback.model,
        override=provider_name,
        api_key_override=api_key,
    )
    resolved_tool_display, tool_display_source = resolve_tool_display(active_core.manifest.ui, fallback.ui, override=tool_display)
    provider, resolved_provider_name = create_provider(
        provider_config=provider_config,
        fake_script=fake_script,
    )
    gate_runner = GateRunner(project_root=project_root)
    mcp_runtime = McpRuntime(home=home, workspace=workspace_scope.root)
    task_worker = RuntimeTaskWorker(control_plane=control_plane, host_work=host_work)
    tool_runtime = ToolRuntime(
        version_store,
        workspace=workspace_scope,
        approval_runtime=approval_runtime,
        global_approval=fallback.approval,
        mcp_runtime=mcp_runtime,
        runtime_timezone=runtime_timezone,
        task_worker=task_worker,
        session_runtime=session_runtime,
    )
    evolution_runtime = EvolutionRuntime(
        core_repository=version_store.core_repository,
        gate_runner=gate_runner,
        evolver_runner=HostEvolverRunner(
            home=home,
            project_root=project_root,
            version_store=version_store,
            core_loader=core_loader,
            host_config=host_config,
            fallback=fallback,
            runtime_timezone=runtime_timezone,
            api_key_override=api_key,
            session_runtime=session_runtime,
            task_worker=task_worker,
        ),
        on_core_changed=approval_runtime.invalidate_core,
    )
    tool_runtime.evolution_runtime = evolution_runtime
    runner_session_id = session_id or utc_id("session_")
    runner = SessionTurnStepRunner(
        home=home,
        version_store=version_store,
        core_loader=core_loader,
        provider=provider,
        tool_runtime=tool_runtime,
        core_id=resolved_core_id,
        session_id=runner_session_id,
        model_override=model,
        model_resolver=lambda core_model: resolve_model_name(core_model, fallback.model, override=model)[0],
        provider_name=resolved_provider_name,
        workspace=str(workspace_scope.root),
        show_system_prompt=host_config.debug.show_system_prompt,
        runtime_timezone=runtime_timezone,
        task_worker=task_worker,
        session_runtime=session_runtime,
        interaction_router=interaction_router,
        prepare_live_core=lambda: version_store.core_repository.prepare_live_for_edit_async(
            validate=lambda agents_root, changed_paths: gate_runner.run(agents_root, changed_paths=changed_paths)
        ),
        principal_scope=None,
        initialize_session=False,
    )
    runtime_recovery_summary = runner.delivery_runtime.recover()
    app = DemiurgeApp(
        home=home,
        project_root=project_root,
        version_store=version_store,
        core_loader=core_loader,
        gate_runner=gate_runner,
        evolution_runtime=evolution_runtime,
        runtime_store=runtime_store,
        control_plane=control_plane,
        host_work=host_work,
        session_runtime=session_runtime,
        interaction_router=interaction_router,
        task_worker=task_worker,
        tool_runtime=tool_runtime,
        approval_runtime=approval_runtime,
        workspace=workspace_scope,
        source_agents_root=source_agents,
        runner=runner,
        provider_name=resolved_provider_name,
        provider_source=provider_config.provider_source,
        provider_api_mode=provider_config.api_mode,
        model_name=resolved_model,
        model_name_source=resolved_model_source,
        base_url=provider_config.base_url,
        base_url_source=provider_config.base_url_source,
        api_key_source=provider_config.api_key_source,
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
        runtime_timezone=runtime_timezone,
        runtime_recovery_summary=runtime_recovery_summary,
        host_config_path=host_config_path,
        fallback_config_path=version_store.fallback_config_path,
        _operator_authority=None,
        _closed=False,
    )
    _ACTIVE_APP_LIFECYCLES[id(app)] = app
    operator_authority = _activate_operator_authority(runtime_store, app)
    app._operator_authority = operator_authority
    try:
        runner.principal_scope = PrincipalScopeResolver(runtime_store).local_operator(
            active_session_id=runner_session_id,
            reason="bootstrap local operator runner",
            allow_unowned_active=True,
        )
        runner._ensure_current_session()
    except Exception:
        _deactivate_operator_authority(runtime_store, app, operator_authority)
        app._operator_authority = None
        app._closed = True
        _ACTIVE_APP_LIFECYCLES.pop(id(app), None)
        raise
    return app


def init_runtime(
    *,
    home: Path | None = None,
    core_id: str = "assistant",
    agents_root: Path | None = None,
    reason: str = "init",
) -> dict[str, object]:
    resolved_home = ensure_dir((home or default_home()).resolve())
    load_runtime_env(resolved_home)
    source_agents = source_agents_root(agents_root)
    host_config_path = resolved_home / "config.yaml"
    host_config_created = write_default_host_config_if_missing(host_config_path)
    version_store = VersionStore(resolved_home)
    pointer = version_store.initialize_repository(source_agents, reason=reason, force=False)
    version_store.ensure_initialized(core_id, source_agents / core_id)
    version_store.ensure_initialized("evolver", source_agents / "evolver")
    core_pointer = version_store.active_pointer(core_id)
    evolver_pointer = version_store.active_pointer("evolver")
    return {
        "home": str(resolved_home),
        "host_config": str(host_config_path),
        "host_config_created": host_config_created,
        "agents_root": str(source_agents),
        "fallback_config": str(version_store.fallback_config_path),
        "active_path": str(version_store.active_core_path(core_id)),
        "core_id": core_id,
        "active_revision": core_pointer.active_revision,
        "previous_revision": core_pointer.previous_revision,
        "evolver_active_path": str(version_store.active_core_path("evolver")),
        "evolver_revision": evolver_pointer.active_revision,
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
    load_runtime_env(resolved_home)
    source_agents = source_agents_root(agents_root)
    version_store = VersionStore(resolved_home)
    version_store.initialize_repository(source_agents, reason=reason, force=False)
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
    if target == "all":
        result = version_store.refresh_repository(source_agents, reason=reason)
        for item in dict.fromkeys(targets):
            path = version_store.fallback_config_path if item == "global" else version_store.active_core_path(item)
            items[item] = {
                "path": str(path),
                "active_revision": result.revision,
                "previous_revision": result.previous_revision,
            }
    else:
        result = version_store.refresh_repository(source_agents, reason=reason)
        for item in dict.fromkeys(targets):
            path = version_store.fallback_config_path if item == "global" else version_store.active_core_path(item)
            items[item] = {
                "path": str(path),
                "active_revision": result.revision,
                "previous_revision": result.previous_revision,
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
    version_store.initialize_repository(source_agents, reason="auto init", force=False)
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
            f"invalid host config {path}: supported fields are runtime.default_core, runtime.timezone, "
            f"channel.busy_mode, ui.user_message_align, ui.demiurge_theme_color, "
            f"ui.user_theme_color, debug.show_system_prompt, providers.*, and packages.repositories.*: {exc}"
        ) from exc
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(
            f"invalid host config {path}: supported fields are runtime.default_core, runtime.timezone, "
            f"channel.busy_mode, ui.user_message_align, ui.demiurge_theme_color, "
            f"ui.user_theme_color, debug.show_system_prompt, providers.*, and packages.repositories.*: {exc}"
        ) from exc
    return config, _host_config_sources(raw)


def default_host_config_dict() -> dict[str, object]:
    return {
        "runtime": {
            "default_core": "assistant",
            "timezone": None,
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
        "providers": {
            "default": None,
            "builtin": {},
            "custom": {},
        },
        "packages": {
            "repositories": {
                "builtin": {
                    "type": "builtin",
                },
            },
        },
    }


def write_host_config(path: Path, config: HostConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(host_config_to_dict(config), sort_keys=False), encoding="utf-8")


def host_config_to_dict(config: HostConfig) -> dict[str, object]:
    return config.model_dump(mode="python", exclude_none=False)


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
        ("runtime", "timezone"),
        ("channel", "busy_mode"),
        ("ui", "user_message_align"),
        ("ui", "demiurge_theme_color"),
        ("ui", "user_theme_color"),
        ("debug", "show_system_prompt"),
        ("providers", "default"),
    ):
        raw_section = raw.get(section)
        if isinstance(raw_section, dict) and key in raw_section:
            sources[f"{section}.{key}"] = f"config.yaml:{section}.{key}"
    raw_providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else None
    raw_builtin = raw_providers.get("builtin") if isinstance(raw_providers, dict) else None
    if isinstance(raw_builtin, dict):
        for provider_id, profile in raw_builtin.items():
            if isinstance(profile, dict):
                for key in profile:
                    sources[f"providers.builtin.{provider_id}.{key}"] = f"config.yaml:providers.builtin.{provider_id}.{key}"
    raw_custom = raw_providers.get("custom") if isinstance(raw_providers, dict) else None
    if isinstance(raw_custom, dict):
        for profile_id, profile in raw_custom.items():
            if isinstance(profile, dict):
                for key in profile:
                    sources[f"providers.custom.{profile_id}.{key}"] = f"config.yaml:providers.custom.{profile_id}.{key}"
    raw_repositories = raw.get("packages", {}).get("repositories") if isinstance(raw.get("packages"), dict) else None
    if isinstance(raw_repositories, dict):
        for alias, repository in raw_repositories.items():
            if isinstance(repository, dict):
                for key in repository:
                    sources[f"packages.repositories.{alias}.{key}"] = f"config.yaml:packages.repositories.{alias}.{key}"
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
    provider_config: ResolvedProviderConfig,
    fake_script: Path | None = None,
) -> tuple[Provider, str]:
    return create_provider_from_config(
        config=ProviderFactoryConfig(
            provider_id=provider_config.provider_id,
            api_mode=provider_config.api_mode,
            base_url=provider_config.base_url,
            api_key=provider_config.api_key,
            runtime_profile=provider_config.runtime_profile,
        ),
        fake_script=fake_script,
    )


def resolve_provider_config(
    host_config: HostConfig,
    model_info: ModelInfo | None = None,
    fallback_model_info: ModelInfo | None = None,
    *,
    override: str | None = None,
    api_key_override: str | None = None,
) -> ResolvedProviderConfig:
    provider_id, provider_source = resolve_provider_id(
        host_config,
        model_info,
        fallback_model_info,
        override=override,
    )
    if provider_id == "fake":
        return ResolvedProviderConfig(
            provider_id="fake",
            provider_source=provider_source,
            api_mode="fake",
            api_mode_source=None,
            base_url=None,
            base_url_source=None,
            api_key=None,
            api_key_source=None,
        )
    profile = resolve_host_provider_profile(host_config, provider_id)
    api_key, api_key_source = resolve_profile_api_key(
        profile,
        provider_id=provider_id,
        override=api_key_override,
    )
    return ResolvedProviderConfig(
        provider_id=provider_id,
        provider_source=provider_source,
        api_mode=profile.api_mode,
        api_mode_source=profile.api_mode_source,
        base_url=profile.base_url,
        base_url_source=profile.base_url_source,
        api_key=api_key,
        api_key_source=api_key_source,
        runtime_profile=profile.runtime_profile,
    )


def resolve_provider_id(
    host_config: HostConfig,
    model_info: ModelInfo | None = None,
    fallback_model_info: ModelInfo | None = None,
    *,
    override: str | None = None,
) -> tuple[str, str]:
    override = _normalize_provider_id(override)
    if override and override != "auto":
        return override, "cli"
    for item, source in (
        (model_info, "agent.yaml:model.provider"),
        (fallback_model_info, "agents/agent.yaml:model.provider"),
    ):
        configured = _normalize_provider_id(item.provider if item else None)
        if configured and configured != "auto":
            return configured, source
    default_provider = _normalize_provider_id(host_config.providers.default)
    if default_provider:
        return default_provider, "config.yaml:providers.default"
    return "fake", "default"


def resolve_host_provider_profile(host_config: HostConfig, provider_id: str) -> ResolvedHostProviderProfile:
    provider_id = _normalize_provider_id(provider_id) or ""
    _validate_provider_id(provider_id)
    runtime_profile = get_builtin_provider_profile(provider_id)
    if runtime_profile is not None:
        override = host_config.providers.builtin.get(provider_id)
        api_key_env = runtime_profile.env_vars[0] if runtime_profile.env_vars else None
        api_key_env_source = f"builtin:{provider_id}.env_vars[0]" if api_key_env else None
        base_url = runtime_profile.base_url
        base_url_source = f"builtin:{provider_id}.base_url"
        api_key = None
        api_key_source = None
        profile_source = f"builtin:{provider_id}"
        if override is not None:
            profile_source = f"config.yaml:providers.builtin.{provider_id}"
            if override.base_url:
                base_url = override.base_url
                base_url_source = f"{profile_source}.base_url"
        return ResolvedHostProviderProfile(
            provider_id=provider_id,
            profile_kind="builtin",
            profile_source=profile_source,
            runtime_profile=runtime_profile,
            api_mode=runtime_profile.api_mode,
            api_mode_source=f"builtin:{provider_id}.api_mode",
            base_url=base_url,
            base_url_source=base_url_source,
            api_key_env=api_key_env,
            api_key_env_source=api_key_env_source,
            api_key=api_key,
            api_key_source=api_key_source,
        )
    custom = host_config.providers.custom.get(provider_id)
    if custom is not None:
        profile_source = f"config.yaml:providers.custom.{provider_id}"
        runtime_profile = ProviderRuntimeProfile(
            provider_id=provider_id,
            display_name=provider_id,
            api_mode=custom.api_mode,
            base_url=custom.base_url,
            env_vars=(custom.api_key_env,) if custom.api_key_env else (),
        )
        return ResolvedHostProviderProfile(
            provider_id=provider_id,
            profile_kind="custom",
            profile_source=profile_source,
            runtime_profile=runtime_profile,
            api_mode=custom.api_mode,
            api_mode_source=f"{profile_source}.api_mode",
            base_url=custom.base_url,
            base_url_source=f"{profile_source}.base_url",
            api_key_env=custom.api_key_env,
            api_key_env_source=f"{profile_source}.api_key_env" if custom.api_key_env else None,
            api_key=custom.api_key,
            api_key_source=f"{profile_source}.api_key" if custom.api_key else None,
        )
    raise ValueError(f"unknown provider profile: {provider_id}")


def resolve_profile_api_key(
    profile: ResolvedHostProviderProfile,
    *,
    provider_id: str,
    override: str | None = None,
) -> tuple[str | None, str | None]:
    if override:
        return override, "cli"
    if profile.api_key_env:
        value = _env_value(profile.api_key_env)
        if value:
            return value, f"env:{profile.api_key_env}"
    if profile.api_key:
        return profile.api_key, profile.api_key_source or f"{profile.profile_source}.api_key"
    return None, None


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
        if item and item.model_name:
            return item.model_name, f"{prefix}.model_name"
    return "fake/demo", "default"


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


def _normalize_provider_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("_", "-")
    return normalized or None


def normalize_provider_profile_id(value: str | None) -> str:
    normalized = _normalize_provider_id(value) or ""
    _validate_provider_id(normalized)
    return normalized


def _validate_provider_id(value: str) -> None:
    if value in {"", "auto"}:
        raise ValueError("provider profile id must not be empty or auto")
    if value == "fake":
        return
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value):
        raise ValueError(f"invalid provider profile id: {value}")


def _env_value(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value
