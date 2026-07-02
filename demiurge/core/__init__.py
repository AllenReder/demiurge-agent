from __future__ import annotations

import importlib.util
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Literal, Mapping

import yaml
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator

from demiurge.util import require_relative_path

BUILTIN_TOOLSETS: dict[str, list[str]] = {
    "coding": [
        "read_file",
        "write_file",
        "patch",
        "search_files",
        "terminal",
        "run_terminal",
        "web_extract",
        "skills_list",
        "skill_view",
        "skill_manage",
        "todo",
        "clarify",
        "session_search",
    ],
    "demiurge_control": [
        "tools_list",
        "task_list",
        "delegate_task",
        "task_status",
        "task_control",
        "yield_until",
        "evolve_core",
        "rollback_core",
    ],
    "schedule": [
        "schedule_manage",
    ],
}


class AgentInfo(BaseModel):
    id: str
    version: str
    parent: str | None = None
    summary: str = ""


class RuntimeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_root: str = "agent"
    max_model_steps: int = Field(default=90, ge=1, le=90)
    workspace: str | None = None

    @field_validator("workspace", mode="before")
    @classmethod
    def _workspace(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("runtime.workspace must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("runtime.workspace must not be empty")
        return normalized


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model_name: str | None = None
    model_options: dict[str, Any] = Field(default_factory=dict)


class UiInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_display: str | None = None


class ChannelBaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    type: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _optional_type(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip().lower().replace("-", "_")
        return text or None


class TelegramChannelConfig(ChannelBaseConfig):
    type: Literal["telegram"] | None = None
    bot_token_env: str | None = None
    bot_token: str | None = None
    bot_username: str | None = None
    allowed_users: list[StrictInt] = Field(default_factory=list)
    allowed_chats: list[StrictInt] = Field(default_factory=list)
    unauthorized_response: Literal["silent", "brief"] = "brief"
    poll_timeout: int = 30
    message_format: Literal["markdown_v2", "plain"] = "markdown_v2"
    register_commands: bool = True
    send_typing: bool = True
    rich_messages: bool = True
    reply_to_mode: Literal["off", "first", "all"] = "off"


class WebhookChannelConfig(ChannelBaseConfig):
    type: Literal["webhook"] | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    path: str = "/demiurge"
    token_env: str | None = "DEMIURGE_WEBHOOK_TOKEN"
    token: str | None = None
    allow_unauthenticated: bool = False
    callback_url_env: str | None = None
    callback_url: str | None = None
    allow_private_callback_urls: bool = False
    allowed_sources: list[str] = Field(default_factory=list)
    delivery_targets: dict[str, str] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        normalized = value.strip() or "/demiurge"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized


class SlackChannelConfig(ChannelBaseConfig):
    type: Literal["slack"] | None = None
    bot_token_env: str | None = "SLACK_BOT_TOKEN"
    bot_token: str | None = None
    signing_secret_env: str | None = "SLACK_SIGNING_SECRET"
    signing_secret: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1, le=65535)
    path: str = "/slack/events"
    bot_user_id: str | None = None
    app_mentions_only: bool = True
    allowed_teams: list[str] = Field(default_factory=list)
    allowed_channels: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        normalized = value.strip() or "/slack/events"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized


class MattermostChannelConfig(ChannelBaseConfig):
    type: Literal["mattermost"] | None = None
    base_url: str | None = None
    token_env: str | None = "MATTERMOST_BOT_TOKEN"
    token: str | None = None
    incoming_webhook_url_env: str | None = None
    incoming_webhook_url: str | None = None
    webhook_token_env: str | None = "MATTERMOST_WEBHOOK_TOKEN"
    webhook_token: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8767, ge=1, le=65535)
    path: str = "/mattermost"
    allowed_channels: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        normalized = value.strip() or "/mattermost"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized


class MatrixChannelConfig(ChannelBaseConfig):
    type: Literal["matrix"] | None = None
    homeserver_url: str | None = None
    access_token_env: str | None = "MATRIX_ACCESS_TOKEN"
    access_token: str | None = None
    user_id: str | None = None
    allowed_rooms: list[str] = Field(default_factory=list)
    poll_timeout: int = Field(default=30, ge=1)


class EmailChannelConfig(ChannelBaseConfig):
    type: Literal["email"] | None = None
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_starttls: bool = True
    smtp_username_env: str | None = "DEMIURGE_SMTP_USERNAME"
    smtp_password_env: str | None = "DEMIURGE_SMTP_PASSWORD"
    smtp_username: str | None = None
    smtp_password: str | None = None
    imap_host: str | None = None
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username_env: str | None = "DEMIURGE_IMAP_USERNAME"
    imap_password_env: str | None = "DEMIURGE_IMAP_PASSWORD"
    imap_username: str | None = None
    imap_password: str | None = None
    mailbox: str = "INBOX"
    from_address: str | None = None
    allowed_senders: list[str] = Field(default_factory=list)
    allowed_recipients: list[str] = Field(default_factory=list)
    trust_from_headers: bool = False
    poll_interval: int = Field(default=30, ge=1)


class UnknownChannelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    type: str | None = None


ChannelConfig = (
    TelegramChannelConfig
    | WebhookChannelConfig
    | SlackChannelConfig
    | MattermostChannelConfig
    | MatrixChannelConfig
    | EmailChannelConfig
    | UnknownChannelConfig
)

_CHANNEL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "telegram": TelegramChannelConfig,
    "webhook": WebhookChannelConfig,
    "slack": SlackChannelConfig,
    "mattermost": MattermostChannelConfig,
    "matrix": MatrixChannelConfig,
    "email": EmailChannelConfig,
}


class ApprovalInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str | None = None
    tools: dict[str, str] = Field(default_factory=dict)
    capabilities: dict[str, str] = Field(default_factory=dict)
    risks: dict[str, str] = Field(default_factory=dict)


class AgentFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelInfo = Field(default_factory=ModelInfo)
    ui: UiInfo = Field(default_factory=UiInfo)
    approval: ApprovalInfo = Field(default_factory=ApprovalInfo)


class DependencyInfo(BaseModel):
    mode: str = "host_shared"
    allow_additional_dependencies: bool = False


class SmokeInfo(BaseModel):
    fake_llm_script: str | None = None


class TestsInfo(BaseModel):
    commands: list[str] = Field(default_factory=list)
    smoke: SmokeInfo = Field(default_factory=SmokeInfo)


class ToolMetadataInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk: str | None = None
    capability: str | None = None
    approval_policy: str | None = None
    model_output_policy: str | None = None
    display_policy: str | None = None
    enabled: bool | None = None


class ToolsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    toolsets: list[str] = Field(default_factory=list)
    metadata: dict[str, ToolMetadataInfo] = Field(default_factory=dict)


class ScheduleModulesInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: list[str] = Field(default_factory=lambda: ["base_input"])
    output: list[str] = Field(default_factory=lambda: ["base_output"])

    @field_validator("input", "output")
    @classmethod
    def _module_list(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("schedule module list must not be empty")
        normalized = [str(item).strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("schedule module ids must not be empty")
        return normalized


class ScheduleDeliveryInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "local"
    channel: str | None = None
    target: str | None = None
    chat_id: StrictInt | None = None

    @field_validator("mode", "channel", mode="before")
    @classmethod
    def _channel_name(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip().lower().replace("-", "_")
        return text or None

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _delivery_target(self) -> "ScheduleDeliveryInfo":
        if not self.mode:
            self.mode = "local"
        if self.mode == "local":
            return self
        if self.mode == "telegram" and self.chat_id is None:
            raise ValueError("telegram schedule delivery requires chat_id")
        if self.mode != "telegram" and self.target is None:
            raise ValueError(f"{self.mode} schedule delivery requires target")
        return self

    @property
    def channel_name(self) -> str:
        return self.channel or self.mode

    @property
    def delivery_target(self) -> str | None:
        if self.chat_id is not None:
            return str(self.chat_id)
        return self.target


class ScheduleManifestInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    schedule: str
    prompt: str
    modules: ScheduleModulesInfo = Field(default_factory=ScheduleModulesInfo)
    delivery: ScheduleDeliveryInfo = Field(default_factory=ScheduleDeliveryInfo)

    @field_validator("schedule")
    @classmethod
    def _cron_expression(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("schedule must not be empty")
        if not croniter.is_valid(normalized):
            raise ValueError(f"invalid cron expression: {normalized}")
        return normalized

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt must not be empty")
        return normalized


class McpToolFilterInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("include", "exclude")
    @classmethod
    def _tool_filter_list(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized


class McpServerManifestInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    tools: McpToolFilterInfo = Field(default_factory=McpToolFilterInfo)
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    approval_policy: Literal["auto", "prompt", "deny"] = "prompt"
    capability: str | None = None
    connect_timeout_seconds: float = Field(default=30, gt=0)
    timeout_seconds: float = Field(default=60, gt=0)
    supports_parallel_tool_calls: bool = False

    @field_validator("command", "cwd", "url", "capability", mode="before")
    @classmethod
    def _optional_string(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("args", mode="before")
    @classmethod
    def _args(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        return [str(item) for item in value]

    @field_validator("env", "headers", mode="before")
    @classmethod
    def _string_map(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value
        return {str(key): str(item) for key, item in value.items()}

    @model_validator(mode="after")
    def _transport_requires_launch_config(self) -> "McpServerManifestInfo":
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires command")
        if self.transport == "streamable_http":
            if not self.url:
                raise ValueError("streamable_http MCP server requires url")
            if not self.url.startswith(("http://", "https://")):
                raise ValueError("streamable_http MCP server url must start with http:// or https://")
        return self


class CoreManifest(BaseModel):
    schema_version: int = 1
    agent: AgentInfo
    runtime: RuntimeInfo = Field(default_factory=RuntimeInfo)
    model: ModelInfo = Field(default_factory=ModelInfo)
    ui: UiInfo = Field(default_factory=UiInfo)
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)
    slots: dict[str, str] = Field(default_factory=dict)
    tools: ToolsInfo = Field(default_factory=ToolsInfo)
    approval: ApprovalInfo = Field(default_factory=ApprovalInfo)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    dependencies: DependencyInfo = Field(default_factory=DependencyInfo)
    tests: TestsInfo = Field(default_factory=TestsInfo)

    @field_validator("channels", mode="before")
    @classmethod
    def _channels(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value
        parsed: dict[str, BaseModel] = {}
        for raw_name, raw_config in value.items():
            name = str(raw_name).strip().lower().replace("-", "_")
            if not name:
                raise ValueError("channel names must not be empty")
            config_data = raw_config or {}
            if not isinstance(config_data, dict):
                raise ValueError(f"channels.{name} must be a mapping")
            channel_type = str(config_data.get("type") or name).strip().lower().replace("-", "_")
            model = _CHANNEL_CONFIG_MODELS.get(channel_type)
            if model is None:
                parsed[name] = UnknownChannelConfig.model_validate({**config_data, "type": channel_type})
                continue
            parsed[name] = model.model_validate({**config_data, "type": channel_type})
        return parsed


@dataclass(slots=True)
class SlotDefinition:
    kind: str
    slot_id: str
    path: Path
    relative_path: str
    manifest: dict[str, Any]
    core_root: Path | None = None
    entrypoint: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    role: str = "extension"
    order: int = 100
    mode: str = "sync"
    timeout_seconds: float | None = None
    failure_policy: str = "soft"
    default_placement: str = "pre_current_user"
    history_policy: str = "persist"


@dataclass(slots=True)
class PhasePipeline:
    serial: list[SlotDefinition] = field(default_factory=list)
    parallel: list[SlotDefinition] = field(default_factory=list)


@dataclass(slots=True)
class SkillDefinition:
    skill_id: str
    name: str
    path: Path
    relative_path: str
    description: str
    content: str
    category: str = "general"
    frontmatter: dict[str, Any] = field(default_factory=dict)
    linked_files: dict[str, list[str]] = field(default_factory=dict)
    packaged: bool = False


@dataclass(slots=True)
class ScheduleDefinition:
    schedule_id: str
    path: Path
    relative_path: str
    manifest: ScheduleManifestInfo
    raw_manifest: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return self.manifest.enabled

    @property
    def schedule(self) -> str:
        return self.manifest.schedule

    @property
    def prompt(self) -> str:
        return self.manifest.prompt

    @property
    def modules(self) -> ScheduleModulesInfo:
        return self.manifest.modules

    @property
    def delivery(self) -> ScheduleDeliveryInfo:
        return self.manifest.delivery


@dataclass(slots=True)
class McpServerDefinition:
    server_id: str
    path: Path
    relative_path: str
    manifest: McpServerManifestInfo
    raw_manifest: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return self.manifest.enabled

    @property
    def capability(self) -> str:
        return self.manifest.capability or f"mcp.call:{self.server_id}"


@dataclass(slots=True)
class LoadedCore:
    root: Path
    manifest_path: Path
    manifest: CoreManifest
    raw_manifest: dict[str, Any]
    soul: str
    bootstrap_slots: list[SlotDefinition]
    bootstrap_pipeline: PhasePipeline
    bootstrap_enabled: bool
    input_slots: list[SlotDefinition]
    output_slots: list[SlotDefinition]
    input_pipeline: PhasePipeline
    output_pipeline: PhasePipeline
    tool_slots: list[SlotDefinition]
    skills: list[SkillDefinition]
    schedules: list[ScheduleDefinition]
    mcp_servers: list[McpServerDefinition]

    @property
    def core_id(self) -> str:
        return self.manifest.agent.id

    @property
    def version(self) -> str:
        return self.manifest.agent.version

    @property
    def builtin_tool_names(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for toolset in self.manifest.tools.toolsets:
            for name in BUILTIN_TOOLSETS[toolset]:
                if name not in seen:
                    names.append(name)
                    seen.add(name)
        return names

    def skill_by_id(self, skill_id: str) -> SkillDefinition | None:
        normalized = skill_id.strip()
        return next(
            (
                skill
                for skill in self.skills
                if skill.skill_id == normalized or skill.name == normalized
            ),
            None,
        )

class CoreLoadError(ValueError):
    pass


class CoreLoader:
    def load(self, core_root: Path) -> LoadedCore:
        core_root = core_root.resolve()
        manifest_path = core_root / "agent.yaml"
        if not manifest_path.exists():
            raise CoreLoadError(f"missing agent.yaml: {manifest_path}")
        try:
            raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            manifest = CoreManifest.model_validate(raw_manifest)
        except (ValidationError, yaml.YAMLError) as exc:
            raise CoreLoadError(f"invalid agent.yaml: {exc}") from exc
        self._validate_toolsets(manifest)
        surface_root = require_relative_path(core_root / manifest.runtime.surface_root, core_root)
        if not surface_root.exists():
            raise CoreLoadError(f"missing authored surface: {surface_root}")

        soul = self._load_soul(core_root, manifest, surface_root)
        slots_manifest = self._load_slots_manifest(core_root, surface_root)
        bootstrap_slots = slots_manifest["bootstrap_slots"]
        bootstrap_pipeline = slots_manifest["bootstrap_pipeline"]
        bootstrap_enabled = bool(bootstrap_slots or bootstrap_pipeline.serial)
        input_slots = slots_manifest["input_slots"]
        output_slots = slots_manifest["output_slots"]
        input_pipeline = slots_manifest["input_pipeline"]
        output_pipeline = slots_manifest["output_pipeline"]
        tool_slots = self._discover_slot_dir(core_root, manifest.slots.get("tools"), "tool")
        skills = self._discover_skills(
            core_root,
            manifest.slots.get("skills") or (surface_root / "skills").relative_to(core_root).as_posix(),
        )
        schedules = self._discover_schedules(
            core_root,
            manifest.slots.get("schedules") or (surface_root / "schedules").relative_to(core_root).as_posix(),
        )
        mcp_servers = self._discover_mcp_servers(
            core_root,
            manifest.slots.get("mcp") or (surface_root / "mcp").relative_to(core_root).as_posix(),
        )
        self._reject_duplicate_ids(bootstrap_slots + input_slots + output_slots + tool_slots)
        self._reject_duplicate_skills(skills)
        self._validate_slots(bootstrap_slots, kind="bootstrap")
        self._validate_io_modules(input_slots, output_slots)
        self._validate_slots(tool_slots, kind="tool")
        self._validate_schedules(manifest, schedules, input_slots=input_slots, output_slots=output_slots)

        return LoadedCore(
            root=core_root,
            manifest_path=manifest_path,
            manifest=manifest,
            raw_manifest=raw_manifest,
            soul=soul,
            bootstrap_slots=bootstrap_slots,
            bootstrap_pipeline=bootstrap_pipeline,
            bootstrap_enabled=bootstrap_enabled,
            input_slots=input_slots,
            output_slots=output_slots,
            input_pipeline=input_pipeline,
            output_pipeline=output_pipeline,
            tool_slots=tool_slots,
            skills=skills,
            schedules=schedules,
            mcp_servers=mcp_servers,
        )

    def _load_soul(self, core_root: Path, manifest: CoreManifest, surface_root: Path) -> str:
        configured = manifest.slots.get("soul")
        candidates: list[Path] = []
        if configured:
            candidates.append(require_relative_path(core_root / configured, core_root))
        candidates.append(surface_root / "SOUL.md")
        parts: list[str] = []
        seen: set[Path] = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            if path.exists() and path.is_file():
                parts.append(path.read_text(encoding="utf-8").strip())
        return "\n\n".join(part for part in parts if part)

    def _load_slots_manifest(self, core_root: Path, surface_root: Path) -> dict[str, Any]:
        pipelines_path = surface_root / "pipelines.yaml"
        if not pipelines_path.exists():
            raise CoreLoadError(f"missing pipelines.yaml: {pipelines_path.relative_to(core_root).as_posix()}")
        try:
            raw = yaml.safe_load(pipelines_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise CoreLoadError(f"invalid pipelines.yaml: {pipelines_path.relative_to(core_root).as_posix()}: {exc}") from exc
        if not isinstance(raw, dict):
            raise CoreLoadError("invalid pipelines.yaml: expected mapping")
        if raw.get("schema_version") != 1:
            raise CoreLoadError("invalid pipelines.yaml: schema_version must be 1")
        unknown_pipeline_phases = sorted(set(raw) - {"schema_version", "bootstrap", "input", "output"})
        if unknown_pipeline_phases:
            raise CoreLoadError(f"invalid pipelines.yaml phase(s): {', '.join(unknown_pipeline_phases)}")

        phase_slots: dict[str, list[SlotDefinition]] = {}
        seen_ids: dict[str, str] = {}
        for phase in ("bootstrap", "input", "output"):
            slots = self._discover_phase_slot_dir(core_root, surface_root, phase)
            for slot in slots:
                prior = seen_ids.get(slot.slot_id)
                if prior:
                    raise CoreLoadError(f"duplicate slot id {slot.slot_id}: {prior}, {slot.relative_path}")
                seen_ids[slot.slot_id] = slot.relative_path
            phase_slots[phase] = sorted(slots, key=lambda item: item.slot_id)

        bootstrap_pipeline = self._load_slots_yaml_pipeline(
            "bootstrap",
            raw.get("bootstrap") or {},
            phase_slots["bootstrap"],
            allow_parallel=False,
        )
        input_pipeline = self._load_slots_yaml_pipeline(
            "input",
            raw.get("input") or {},
            phase_slots["input"],
            allow_parallel=True,
        )
        output_pipeline = self._load_slots_yaml_pipeline(
            "output",
            raw.get("output") or {},
            phase_slots["output"],
            allow_parallel=True,
        )
        return {
            "bootstrap_slots": phase_slots["bootstrap"],
            "bootstrap_pipeline": bootstrap_pipeline,
            "input_slots": phase_slots["input"],
            "input_pipeline": input_pipeline,
            "output_slots": phase_slots["output"],
            "output_pipeline": output_pipeline,
        }

    def _discover_phase_slot_dir(self, core_root: Path, surface_root: Path, phase: str) -> list[SlotDefinition]:
        slot_root = require_relative_path(surface_root / phase, core_root)
        if not slot_root.exists():
            return []
        if not slot_root.is_dir():
            raise CoreLoadError(f"{phase} slot root is not a directory: {slot_root}")
        slots: list[SlotDefinition] = []
        for child in sorted(slot_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            manifest_path = child / "slot.yaml"
            if not manifest_path.exists():
                continue
            slots.append(
                self._slot_from_slot_yaml(
                    core_root=core_root,
                    phase=phase,
                    slot_id=child.name,
                    slot_dir=child,
                    manifest_path=manifest_path,
                )
            )
        return slots

    def _slot_from_slot_yaml(
        self,
        *,
        core_root: Path,
        phase: str,
        slot_id: str,
        slot_dir: Path,
        manifest_path: Path,
    ) -> SlotDefinition:
        if not slot_id.strip():
            raise CoreLoadError(f"invalid {phase} slot: slot id must not be empty")
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise CoreLoadError(f"invalid slot.yaml: {manifest_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise CoreLoadError(f"invalid slot.yaml: {manifest_path}: expected mapping")
        unknown_keys = sorted(
            set(raw)
            - {
                "entrypoint",
                "description",
                "input_schema",
                "capabilities",
                "timeout_seconds",
                "failure_policy",
                "default_placement",
                "history_policy",
            }
        )
        if unknown_keys:
            raise CoreLoadError(
                f"invalid slot.yaml {manifest_path.relative_to(core_root).as_posix()} key(s): {', '.join(unknown_keys)}"
            )
        slot_dir = require_relative_path(slot_dir, core_root)
        entrypoint = raw.get("entrypoint") or "module:process"
        rel = slot_dir.relative_to(core_root).as_posix()
        return SlotDefinition(
            kind=phase,
            slot_id=slot_id,
            path=slot_dir,
            relative_path=rel,
            manifest=dict(raw),
            core_root=core_root,
            entrypoint=str(entrypoint),
            description=str(raw.get("description") or ""),
            input_schema=raw.get("input_schema", {}) or {},
            capabilities=[str(item) for item in (raw.get("capabilities") or [])],
            timeout_seconds=(float(raw["timeout_seconds"]) if raw.get("timeout_seconds") is not None else None),
            failure_policy=str(raw.get("failure_policy") or "soft"),
            default_placement=str(raw.get("default_placement") or "pre_current_user"),
            history_policy=str(raw.get("history_policy") or "persist"),
        )

    def _load_slots_yaml_pipeline(
        self,
        phase: str,
        raw: Any,
        slots: list[SlotDefinition],
        *,
        allow_parallel: bool,
    ) -> PhasePipeline:
        if not isinstance(raw, dict):
            raise CoreLoadError(f"invalid pipelines.yaml {phase}: expected mapping")
        allowed = {"serial", "parallel"} if allow_parallel else {"serial"}
        unknown_keys = sorted(set(raw) - allowed)
        if unknown_keys:
            raise CoreLoadError(f"invalid pipelines.yaml {phase} key(s): {', '.join(unknown_keys)}")
        slots_by_id = {slot.slot_id: slot for slot in slots}
        seen: dict[str, str] = {}

        def resolve_group(name: str) -> list[SlotDefinition]:
            values = raw.get(name) or []
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise CoreLoadError(f"invalid pipelines.yaml {phase}.{name}: expected list of slot ids")
            resolved: list[SlotDefinition] = []
            for slot_id in values:
                prior = seen.get(slot_id)
                if prior:
                    raise CoreLoadError(f"duplicate {phase} pipeline slot {slot_id}: {prior}, {name}")
                slot = slots_by_id.get(slot_id)
                if slot is None:
                    raise CoreLoadError(f"unknown {phase} pipeline slot: {slot_id}")
                seen[slot_id] = name
                resolved.append(slot)
            return resolved

        return PhasePipeline(
            serial=resolve_group("serial"),
            parallel=resolve_group("parallel") if allow_parallel else [],
        )

    def _discover_slot_dir(
        self,
        core_root: Path,
        configured: str | None,
        kind: str,
    ) -> list[SlotDefinition]:
        if not configured:
            return []
        slot_root = require_relative_path(core_root / configured, core_root)
        if not slot_root.exists():
            return []
        if not slot_root.is_dir():
            raise CoreLoadError(f"{kind} slot root is not a directory: {slot_root}")
        slots: list[SlotDefinition] = []
        for child in sorted(slot_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            metadata_name = "tool.yaml" if kind == "tool" else "slot.yaml"
            slot_manifest_path = child / metadata_name
            if not slot_manifest_path.exists():
                continue
            try:
                slot_manifest = yaml.safe_load(slot_manifest_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise CoreLoadError(f"invalid {metadata_name}: {slot_manifest_path}: {exc}") from exc
            if not isinstance(slot_manifest, dict):
                raise CoreLoadError(f"invalid {metadata_name}: {slot_manifest_path}: expected mapping")
            self._validate_discovered_slot_manifest(kind, slot_manifest_path, slot_manifest, core_root)
            rel = child.relative_to(core_root).as_posix()
            slots.append(
                SlotDefinition(
                    kind=kind,
                    slot_id=child.name,
                    path=require_relative_path(child, core_root),
                    relative_path=rel,
                    manifest=slot_manifest,
                    core_root=core_root,
                    entrypoint=slot_manifest.get("entrypoint") or ("module:execute" if kind == "tool" else None),
                    description=slot_manifest.get("description", ""),
                    input_schema=slot_manifest.get("input_schema", {}) or {},
                    capabilities=list(slot_manifest.get("capabilities", []) or []),
                    timeout_seconds=(
                        float(slot_manifest["timeout_seconds"])
                        if slot_manifest.get("timeout_seconds") is not None
                        else None
                    ),
                    failure_policy=str(slot_manifest.get("failure_policy") or "soft"),
                    default_placement=str(slot_manifest.get("default_placement") or "pre_current_user"),
                    history_policy=str(slot_manifest.get("history_policy") or "persist"),
                )
            )
        return slots

    def _validate_discovered_slot_manifest(
        self,
        kind: str,
        path: Path,
        manifest: Mapping[str, Any],
        core_root: Path,
    ) -> None:
        if kind == "tool":
            allowed = {
                "entrypoint",
                "description",
                "input_schema",
                "risk",
                "capability",
                "approval_policy",
                "display_policy",
                "model_output_policy",
                "capabilities",
            }
            metadata_name = "tool.yaml"
        else:
            allowed = {
                "entrypoint",
                "description",
                "input_schema",
                "capabilities",
                "timeout_seconds",
                "failure_policy",
                "default_placement",
                "history_policy",
            }
            metadata_name = "slot.yaml"
        unknown_keys = sorted(set(manifest) - allowed)
        if unknown_keys:
            rel = path.relative_to(core_root).as_posix()
            raise CoreLoadError(f"invalid {metadata_name} {rel} key(s): {', '.join(unknown_keys)}")

    def _validate_toolsets(self, manifest: CoreManifest) -> None:
        unknown = sorted(set(manifest.tools.toolsets) - set(BUILTIN_TOOLSETS))
        if unknown:
            raise CoreLoadError(f"unknown toolset(s): {', '.join(unknown)}")

    def _reject_duplicate_ids(self, slots: list[SlotDefinition]) -> None:
        seen: dict[str, str] = {}
        for slot in slots:
            prior = seen.get(slot.slot_id)
            if prior:
                raise CoreLoadError(f"duplicate slot id {slot.slot_id}: {prior}, {slot.relative_path}")
            seen[slot.slot_id] = slot.relative_path

    def _discover_skills(self, core_root: Path, configured: str | None) -> list[SkillDefinition]:
        if not configured:
            return []
        skill_root = require_relative_path(core_root / configured, core_root)
        if not skill_root.exists():
            return []
        if not skill_root.is_dir():
            raise CoreLoadError(f"skill root is not a directory: {skill_root}")
        skills: list[SkillDefinition] = []
        for child in sorted(skill_root.iterdir(), key=lambda item: item.name):
            if child.is_file() and child.suffix.lower() == ".md":
                content = child.read_text(encoding="utf-8")
                frontmatter, body = self._parse_skill_content(content)
                description = self._skill_description(frontmatter, body)
                name = str(frontmatter.get("name") or child.stem).strip()
                category = str(frontmatter.get("category") or "general").strip() or "general"
                skills.append(
                    SkillDefinition(
                        skill_id=child.stem,
                        name=name,
                        path=require_relative_path(child, core_root),
                        relative_path=child.relative_to(core_root).as_posix(),
                        description=description,
                        content=content,
                        category=category,
                        frontmatter=frontmatter,
                    )
                )
                continue
        for skill_file in sorted(skill_root.rglob("SKILL.md"), key=lambda item: item.as_posix()):
            if not skill_file.is_file() or skill_file.is_symlink():
                continue
            if any(part.startswith(".") for part in skill_file.relative_to(skill_root).parts):
                continue
            skill_dir = skill_file.parent
            content = skill_file.read_text(encoding="utf-8")
            frontmatter, body = self._parse_skill_content(content)
            description = self._skill_description(frontmatter, body)
            name = str(frontmatter.get("name") or skill_dir.name).strip()
            category = str(frontmatter.get("category") or self._skill_category(skill_root, skill_dir)).strip() or "general"
            skills.append(
                SkillDefinition(
                    skill_id=skill_dir.name,
                    name=name,
                    path=require_relative_path(skill_file, core_root),
                    relative_path=skill_file.relative_to(core_root).as_posix(),
                    description=description,
                    content=content,
                    category=category,
                    frontmatter=frontmatter,
                    linked_files=self._discover_skill_linked_files(core_root, skill_dir),
                    packaged=True,
                )
            )
        return sorted(skills, key=lambda skill: skill.skill_id)

    def _reject_duplicate_skills(self, skills: list[SkillDefinition]) -> None:
        seen: dict[str, str] = {}
        for skill in skills:
            for key in {skill.skill_id, skill.name}:
                prior = seen.get(key)
                if prior:
                    raise CoreLoadError(f"duplicate skill id {key}: {prior}, {skill.relative_path}")
                seen[key] = skill.relative_path

    def _discover_schedules(self, core_root: Path, configured: str | None) -> list[ScheduleDefinition]:
        if not configured:
            return []
        schedule_root = require_relative_path(core_root / configured, core_root)
        if not schedule_root.exists():
            return []
        if not schedule_root.is_dir():
            raise CoreLoadError(f"schedule root is not a directory: {schedule_root}")
        schedules: list[ScheduleDefinition] = []
        seen: dict[str, str] = {}
        for path in sorted(schedule_root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            schedule_id = path.stem
            prior = seen.get(schedule_id)
            rel = path.relative_to(core_root).as_posix()
            if prior:
                raise CoreLoadError(f"duplicate schedule id {schedule_id}: {prior}, {rel}")
            seen[schedule_id] = rel
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise CoreLoadError(f"invalid schedule yaml: {rel}: {exc}") from exc
            if not isinstance(raw, dict):
                raise CoreLoadError(f"invalid schedule yaml: {rel}: expected mapping")
            try:
                manifest = ScheduleManifestInfo.model_validate(raw)
            except ValidationError as exc:
                raise CoreLoadError(f"invalid schedule {rel}: {exc}") from exc
            schedules.append(
                ScheduleDefinition(
                    schedule_id=schedule_id,
                    path=require_relative_path(path, core_root),
                    relative_path=rel,
                    manifest=manifest,
                    raw_manifest=raw,
                )
            )
        return schedules

    def _discover_mcp_servers(self, core_root: Path, configured: str | None) -> list[McpServerDefinition]:
        if not configured:
            return []
        mcp_root = require_relative_path(core_root / configured, core_root)
        if not mcp_root.exists():
            return []
        if not mcp_root.is_dir():
            raise CoreLoadError(f"MCP root is not a directory: {mcp_root}")
        servers: list[McpServerDefinition] = []
        seen: dict[str, str] = {}
        for path in sorted(mcp_root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            server_id = path.stem
            rel = path.relative_to(core_root).as_posix()
            prior = seen.get(server_id)
            if prior:
                raise CoreLoadError(f"duplicate MCP server id {server_id}: {prior}, {rel}")
            seen[server_id] = rel
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise CoreLoadError(f"invalid MCP server yaml: {rel}: {exc}") from exc
            if not isinstance(raw, dict):
                raise CoreLoadError(f"invalid MCP server yaml: {rel}: expected mapping")
            try:
                manifest = McpServerManifestInfo.model_validate(raw)
            except ValidationError as exc:
                raise CoreLoadError(f"invalid MCP server {rel}: {exc}") from exc
            servers.append(
                McpServerDefinition(
                    server_id=server_id,
                    path=require_relative_path(path, core_root),
                    relative_path=rel,
                    manifest=manifest,
                    raw_manifest=raw,
                )
            )
        return servers

    def _validate_io_modules(self, input_slots: list[SlotDefinition], output_slots: list[SlotDefinition]) -> None:
        self._validate_slots(input_slots, kind="input")
        self._validate_slots(output_slots, kind="output")

    def _validate_schedules(
        self,
        manifest: CoreManifest,
        schedules: list[ScheduleDefinition],
        *,
        input_slots: list[SlotDefinition],
        output_slots: list[SlotDefinition],
    ) -> None:
        input_ids = {slot.slot_id for slot in input_slots}
        output_ids = {slot.slot_id for slot in output_slots}
        for schedule in schedules:
            self._validate_schedule_modules(schedule, "input", schedule.modules.input, input_ids)
            self._validate_schedule_modules(schedule, "output", schedule.modules.output, output_ids)
            if schedule.delivery.mode != "local":
                self._validate_channel_schedule_delivery(manifest, schedule)

    def _validate_schedule_modules(
        self,
        schedule: ScheduleDefinition,
        kind: str,
        values: list[str],
        known_ids: set[str],
    ) -> None:
        seen: set[str] = set()
        for module_id in values:
            if module_id in seen:
                raise CoreLoadError(
                    f"duplicate {kind} schedule module {module_id}: {schedule.relative_path}"
                )
            seen.add(module_id)
            if module_id not in known_ids:
                raise CoreLoadError(
                    f"unknown {kind} schedule module {module_id}: {schedule.relative_path}"
                )

    def _validate_channel_schedule_delivery(self, manifest: CoreManifest, schedule: ScheduleDefinition) -> None:
        channel = schedule.delivery.channel_name
        config = manifest.channels.get(channel)
        if config is None:
            raise CoreLoadError(f"{channel} schedule delivery requires channels.{channel}: {schedule.relative_path}")
        try:
            from demiurge.channels.registry import validate_schedule_target

            validate_schedule_target(channel, config, schedule.delivery)
        except Exception as exc:
            raise CoreLoadError(f"{channel} schedule delivery invalid for {schedule.relative_path}: {exc}") from exc

    def _validate_slots(self, slots: list[SlotDefinition], *, kind: str) -> None:
        for slot in slots:
            if slot.failure_policy not in {"soft", "hard"}:
                raise CoreLoadError(
                    f"invalid {kind} module failure_policy for {slot.relative_path}: {slot.failure_policy}"
                )
            if slot.history_policy not in {"persist", "model_hidden", "transient"}:
                raise CoreLoadError(
                    f"invalid {kind} module history_policy for {slot.relative_path}: {slot.history_policy}"
                )

    def _parse_skill_content(self, content: str) -> tuple[dict[str, Any], str]:
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError as exc:
                    raise CoreLoadError(f"invalid skill frontmatter: {exc}") from exc
                if not isinstance(frontmatter, dict):
                    frontmatter = {}
                return frontmatter, parts[2]
        return {}, content

    def _skill_description(self, frontmatter: dict[str, Any], body: str) -> str:
        description = frontmatter.get("description")
        if description:
            return str(description).strip()
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped in {"---", "..."}:
                continue
            return stripped.lstrip("#").strip()
        return ""

    def _skill_category(self, skill_root: Path, skill_dir: Path) -> str:
        try:
            parent = skill_dir.relative_to(skill_root).parent
        except ValueError:
            return "general"
        if str(parent) == ".":
            return "general"
        return parent.as_posix()

    def _discover_skill_linked_files(self, core_root: Path, skill_dir: Path) -> dict[str, list[str]]:
        linked: dict[str, list[str]] = {}
        for dirname in ("references", "templates", "scripts", "assets"):
            directory = skill_dir / dirname
            if not directory.exists() or not directory.is_dir():
                continue
            files: list[str] = []
            for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
                if not path.is_file() or path.is_symlink():
                    continue
                resolved = require_relative_path(path, skill_dir)
                require_relative_path(resolved, core_root)
                files.append(resolved.relative_to(skill_dir).as_posix())
            if files:
                linked[dirname] = files
        return linked


def load_slot_callable(slot: SlotDefinition) -> Callable[..., Any]:
    if not slot.entrypoint:
        raise CoreLoadError(f"slot missing entrypoint: {slot.relative_path}")
    module_name, sep, attr_name = slot.entrypoint.partition(":")
    if not sep or not module_name or not attr_name:
        raise CoreLoadError(f"invalid entrypoint for {slot.relative_path}: {slot.entrypoint}")
    module = _load_module_from_slot(slot, module_name)
    target = getattr(module, attr_name, None)
    if not callable(target):
        raise CoreLoadError(f"entrypoint is not callable: {slot.entrypoint}")
    return target


def _load_module_from_slot(slot: SlotDefinition, module_name: str) -> ModuleType:
    import_name = module_name
    if module_name.endswith(".py") or "/" in module_name:
        if slot.core_root is None:
            raise CoreLoadError(f"path entrypoint requires core root: {slot.entrypoint}")
        module_path = require_relative_path(slot.core_root / module_name, slot.core_root)
        import_name = module_path.stem
    else:
        module_path = slot.path / (module_name.replace(".", "/") + ".py")
    if not module_path.exists():
        raise CoreLoadError(f"slot module not found: {module_path}")
    package_name = _slot_package_name(slot)
    _ensure_slot_package(package_name, _slot_package_paths(slot))
    _ensure_slot_parent_packages(package_name, import_name, slot.path)
    unique_name = f"{package_name}.{import_name}"
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    if spec is None or spec.loader is None:
        raise CoreLoadError(f"could not load module spec: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


def _slot_package_name(slot: SlotDefinition) -> str:
    label = re.sub(r"[^0-9a-zA-Z_]", "_", f"{slot.kind}_{slot.slot_id}").strip("_") or "slot"
    digest = hashlib.sha256(str(slot.path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"_demiurge_slot_{label}_{digest}"


def _slot_package_paths(slot: SlotDefinition) -> list[Path]:
    paths = [slot.path]
    parts = Path(slot.relative_path).parts
    if len(parts) >= 3 and parts[0] == "agent":
        core_root = slot.path
        for _ in parts:
            core_root = core_root.parent
        lib_root = core_root / "agent" / "lib"
        if lib_root.exists() and lib_root.is_dir():
            paths.append(lib_root)
    return paths


def _ensure_slot_package(package_name: str, package_paths: list[Path]) -> None:
    package = sys.modules.get(package_name)
    if package is None:
        package = ModuleType(package_name)
        package.__package__ = package_name
        sys.modules[package_name] = package
    package.__path__ = [str(path) for path in package_paths]  # type: ignore[attr-defined]


def _ensure_slot_parent_packages(package_name: str, module_name: str, slot_path: Path) -> None:
    parts = module_name.split(".")[:-1]
    current_name = package_name
    current_path = slot_path
    for part in parts:
        current_name = f"{current_name}.{part}"
        current_path = current_path / part
        package = sys.modules.get(current_name)
        if package is None:
            package = ModuleType(current_name)
            package.__package__ = current_name
            sys.modules[current_name] = package
        package.__path__ = [str(current_path)]  # type: ignore[attr-defined]
