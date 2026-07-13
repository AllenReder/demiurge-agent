from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping

from demiurge.providers import ToolCall, ToolDefinition
from demiurge.sdk import ToolResult
from demiurge.security.workspace import DEFAULT_READ_LIMIT_CHARS, DEFAULT_TOOL_OUTPUT_LIMIT_CHARS


def _schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


BUILTIN_TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "evolve_core": ToolDefinition(
        name="evolve_core",
        description=(
            "Manage a host-owned evolve change set for the active Agent Core tree. "
            "Use start to create a run, review to gate and create a proposal revision, "
            "promote to advance live, and discard to remove a run."
        ),
        input_schema=_schema(
            {
                "action": {"type": "string", "enum": ["start", "review", "promote", "discard"], "default": "start"},
                "goal": {"type": "string"},
                "run_id": {"type": "string"},
                "background": {"type": "boolean", "default": False},
                "notify_on_complete": {"type": "boolean", "default": True},
                "reason": {"type": "string"},
            }
        ),
    ),
    "rollback_core": ToolDefinition(
        name="rollback_core",
        description="Create a rollback commit for the Agent Core tree. The new revision takes effect on the next turn.",
        input_schema=_schema(
            {
                "target": {"type": "string", "default": "previous"},
                "reason": {"type": "string"},
            }
        ),
    ),
    "read_file": ToolDefinition(
        name="read_file",
        description=(
            "Read a host-visible text file. Workspace reads are auto-approved when non-sensitive; "
            "outside-workspace or sensitive reads require approval. Use offset and limit for large files."
        ),
        input_schema=_schema(
            {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": DEFAULT_READ_LIMIT_CHARS},
            },
            required=["path"],
        ),
    ),
    "search_files": ToolDefinition(
        name="search_files",
        description=(
            "Search host-visible files by content or filename. Workspace searches are auto-approved when "
            "non-sensitive; outside-workspace or sensitive searches require approval. Use target='content' "
            "for text matches, target='name' for file discovery, or target='both' when either is useful."
        ),
        input_schema=_schema(
            {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "target": {"type": "string", "enum": ["content", "name", "both"], "default": "content"},
                "pattern": {"type": "string", "default": "*"},
                "case_sensitive": {"type": "boolean", "default": True},
                "max_results": {"type": "integer", "default": 50},
                "include_sensitive": {"type": "boolean", "default": False},
            }
        ),
    ),
    "write_file": ToolDefinition(
        name="write_file",
        description=(
            "Write text to a workspace file, replacing existing content. Creates parent directories by default. "
            "Use patch for targeted edits."
        ),
        input_schema=_schema(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "create_parent_dirs": {"type": "boolean", "default": True},
            },
            required=["path", "content"],
        ),
    ),
    "patch": ToolDefinition(
        name="patch",
        description=(
            "Apply an exact text replacement to a workspace file. Use this for targeted edits. "
            "Returns a unified diff."
        ),
        input_schema=_schema(
            {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "count": {"type": "integer", "default": -1},
            },
            required=["path", "old", "new"],
        ),
    ),
    "terminal": ToolDefinition(
        name="terminal",
        description=(
            "Run a shell command inside the configured workspace. Set background=true for long-running commands "
            "and use task_status, task_list, or task_control with the returned task_id to inspect or stop them."
        ),
        input_schema=_schema(
            {
                "command": {"type": "string"},
                "cwd": {"type": "string", "default": "."},
                "timeout_seconds": {"type": "integer", "default": 30},
                "background": {"type": "boolean", "default": False},
                "notify_on_complete": {"type": "boolean", "default": True},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "secret_bindings": {
                    "type": "array",
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "pattern": "^env:[A-Za-z_][A-Za-z0-9_]*$"},
                            "target": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
                            "expires_in_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                        },
                        "required": ["source"],
                        "additionalProperties": False,
                    },
                },
            },
            required=["command"],
        ),
    ),
    "task_list": ToolDefinition(
        name="task_list",
        description="List controllable background runtime tasks for the current session.",
        input_schema=_schema(
            {
                "kind": {"type": "string", "enum": ["terminal.exec", "evolver.run", "agent.spawn"]},
                "include_completed": {"type": "boolean", "default": True},
            }
        ),
    ),
    "delegate_task": ToolDefinition(
        name="delegate_task",
        description=(
            "Spawn a child agent task. Child output is evidence for the parent by default; it is not delivered "
            "directly to the user."
        ),
        input_schema=_schema(
            {
                "goal": {"type": "string"},
                "core_id": {"type": "string"},
                "context_mode": {"type": "string", "enum": ["isolated", "fork"], "default": "isolated"},
                "notify_policy": {"type": "string", "enum": ["return_to_parent", "silent"], "default": "return_to_parent"},
                "max_depth": {"type": "integer"},
                "tools": {
                    "anyOf": [
                        {"type": "string", "enum": ["all", "none"]},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "default": "all",
                },
                "input_slots": {
                    "anyOf": [
                        {"type": "string", "enum": ["all"]},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "default": ["base_input"],
                },
                "output_slots": {
                    "anyOf": [
                        {"type": "string", "enum": ["all"]},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "default": ["base_output"],
                },
                "use_bootstrap": {"type": "boolean", "default": False},
            },
            required=["goal"],
        ),
    ),
    "task_status": ToolDefinition(
        name="task_status",
        description="Inspect a delegated task or runtime control-plane task.",
        input_schema=_schema(
            {"task_id": {"type": "string"}},
            required=["task_id"],
        ),
    ),
    "task_control": ToolDefinition(
        name="task_control",
        description="Cancel a delegated task or background runtime task.",
        input_schema=_schema(
            {
                "task_id": {"type": "string"},
                "command": {
                    "type": "string",
                    "enum": ["cancel"],
                    "default": "cancel",
                },
            },
            required=["task_id"],
        ),
    ),
    "yield_until": ToolDefinition(
        name="yield_until",
        description="Wait briefly for a delegated/background task to complete and return its status.",
        input_schema=_schema(
            {
                "task_id": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 30},
            },
            required=["task_id"],
        ),
    ),
    "skills_list": ToolDefinition(
        name="skills_list",
        description="List available skills with minimal metadata. Use skill_view(name) to load full content.",
        input_schema=_schema({"category": {"type": "string"}}),
    ),
    "skill_view": ToolDefinition(
        name="skill_view",
        description="Load a skill's full content or a linked file inside references/templates/scripts/assets.",
        input_schema=_schema(
            {
                "name": {"type": "string"},
                "file_path": {"type": "string"},
            },
            required=["name"],
        ),
    ),
    "skill_manage": ToolDefinition(
        name="skill_manage",
        description=(
            "Create, update, patch, delete, or manage support files for skills in the active runtime core's "
            "skills directory. Support files must live under references/, templates/, scripts/, or assets/."
        ),
        input_schema=_schema(
            {
                "action": {"type": "string", "enum": ["create", "update", "delete", "patch", "write_file", "remove_file"]},
                "name": {"type": "string"},
                "content": {"type": "string"},
                "file_path": {"type": "string"},
                "file_content": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            required=["action", "name"],
        ),
    ),
    "todo": ToolDefinition(
        name="todo",
        description="Maintain a small per-session todo list for multi-step work. Add, list, complete, or clear items.",
        input_schema=_schema(
            {
                "action": {"type": "string", "enum": ["add", "list", "complete", "clear"], "default": "list"},
                "text": {"type": "string"},
                "index": {"type": "integer"},
            }
        ),
    ),
    "clarify": ToolDefinition(
        name="clarify",
        description=(
            "Ask the user a question when clarification, feedback, or a decision is needed before proceeding. "
            "Put selectable options in choices instead of numbering them in the question text."
        ),
        input_schema=_schema(
            {
                "question": {"type": "string"},
                "choices": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            },
            required=["question"],
        ),
    ),
    "web_extract": ToolDefinition(
        name="web_extract",
        description="Fetch and extract text from a URL. Use max_chars to bound the text returned to the model.",
        input_schema=_schema(
            {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 20},
                "max_chars": {"type": "integer", "default": DEFAULT_TOOL_OUTPUT_LIMIT_CHARS},
            },
            required=["url"],
        ),
    ),
    "session_search": ToolDefinition(
        name="session_search",
        description="Search or browse local session message history.",
        input_schema=_schema(
            {
                "query": {"type": "string"},
                "session_id": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            }
        ),
    ),
    "schedule_manage": ToolDefinition(
        name="schedule_manage",
        description=(
            "List, create, update, enable, disable, or delete authored cron schedules in the active agent core. "
            "This manages agent/schedules/*.yaml, not runtime-created tasks. "
            "Create and update accept only standard cron expressions and self-contained prompts."
        ),
        input_schema=_schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "update", "enable", "disable", "delete"],
                    "default": "list",
                },
                "schedule_id": {
                    "type": "string",
                    "description": "Required for update, enable, disable, and delete. Optional for create.",
                },
                "schedule": {
                    "type": "string",
                    "description": "Standard cron expression. Required for create; optional for update.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Self-contained prompt for the scheduled run. Required for create; optional for update.",
                },
            },
            required=["action"],
        ),
    ),
    "tools_list": ToolDefinition(
        name="tools_list",
        description="List the tools currently visible to this agent core.",
        input_schema=_schema({}),
    ),
}


BUILTIN_TOOL_METADATA: dict[str, dict[str, Any]] = {
    "evolve_core": {"risk": "high", "capability": "tool.call:evolve_core", "approval_policy": "prompt"},
    "rollback_core": {"risk": "high", "capability": "tool.call:rollback_core", "approval_policy": "prompt"},
    "read_file": {"risk": "low", "capability": "fs.read", "approval_policy": "auto"},
    "search_files": {"risk": "low", "capability": "fs.read", "approval_policy": "auto"},
    "write_file": {"risk": "medium", "capability": "fs.write", "approval_policy": "prompt"},
    "patch": {"risk": "medium", "capability": "fs.write", "approval_policy": "prompt"},
    "terminal": {"risk": "high", "capability": "terminal.exec", "approval_policy": "prompt"},
    "task_list": {"risk": "low", "capability": "task.control", "approval_policy": "auto"},
    "delegate_task": {"risk": "medium", "approval_policy": "auto"},
    "task_status": {"risk": "low", "capability": "task.control", "approval_policy": "auto"},
    "task_control": {"risk": "medium", "capability": "task.control", "approval_policy": "auto"},
    "yield_until": {"risk": "low", "capability": "task.control", "approval_policy": "auto"},
    "skills_list": {"risk": "low", "approval_policy": "auto", "model_output_policy": "current_turn"},
    "skill_view": {"risk": "low", "approval_policy": "auto", "model_output_policy": "current_turn"},
    "skill_manage": {"risk": "medium", "capability": "fs.write", "approval_policy": "prompt"},
    "todo": {"risk": "low", "approval_policy": "auto"},
    "clarify": {"risk": "low", "approval_policy": "auto"},
    "web_extract": {"risk": "medium", "capability": "network.fetch", "approval_policy": "prompt"},
    "session_search": {
        "risk": "medium",
        "capability": "session.read",
        "approval_policy": "prompt",
        "model_output_policy": "current_turn",
    },
    "schedule_manage": {"risk": "high", "capability": "schedule.manage", "approval_policy": "prompt"},
    "tools_list": {"risk": "low", "approval_policy": "auto", "model_output_policy": "current_turn"},
}


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
APPROVAL_ORDER = {"auto": 0, "prompt": 1, "deny": 2}


class ToolRegistryCollisionError(ValueError):
    """Raised when two effect sources expose the same model-visible name."""


EffectStatus = Literal[
    "succeeded",
    "denied",
    "invalid",
    "not_found",
    "failed",
]
EffectOrigin = Literal["model", "host", "authored"]


@dataclass(frozen=True, slots=True)
class EffectError:
    code: EffectStatus
    message: str
    execution_started: bool
    provenance: str


@dataclass(frozen=True, slots=True)
class EffectResult:
    entry: ResolvedEffectEntry | None
    status: EffectStatus
    result: ToolResult
    error: EffectError | None = None

    def __post_init__(self) -> None:
        if self.entry is None and self.status != "not_found":
            raise ValueError("only not_found EffectResult may omit a resolved entry")
        if self.status == "succeeded" and self.result.is_error:
            raise ValueError("succeeded EffectResult cannot contain an error ToolResult")
        if self.status != "succeeded" and not self.result.is_error:
            raise ValueError("failed EffectResult must contain an error ToolResult")
        if (self.status == "succeeded") != (self.error is None):
            raise ValueError("EffectResult error must match its status")

    @classmethod
    def normalize(
        cls,
        entry: ResolvedEffectEntry,
        result: ToolResult,
    ) -> EffectResult:
        if not result.is_error:
            return cls(entry=entry, status="succeeded", result=result)
        data = result.data if isinstance(result.data, Mapping) else {}
        execution_started = bool(data.get("executionStarted", False))
        approval = data.get("approval")
        denial = data.get("denial")
        if (
            denial in {"approval", "capability", "policy"}
            or (
                isinstance(approval, Mapping)
                and approval.get("value") == "deny"
            )
        ):
            status: EffectStatus = "denied"
        elif result.content.startswith("tool not found or not allowed:"):
            status = "not_found"
        elif execution_started:
            status = "failed"
        else:
            status = "invalid"
        return cls(
            entry=entry,
            status=status,
            result=result,
            error=EffectError(
                code=status,
                message=result.content,
                execution_started=execution_started,
                provenance=entry.provenance,
            ),
        )

    @classmethod
    def not_found(
        cls,
        *,
        name: str,
        core_id: str,
        core_revision: str,
    ) -> EffectResult:
        result = ToolResult(
            content=f"tool not found or not allowed: {name}",
            is_error=True,
            data={"executionStarted": False},
        )
        return cls(
            entry=None,
            status="not_found",
            result=result,
            error=EffectError(
                code="not_found",
                message=result.content,
                execution_started=False,
                provenance=f"unresolved:{core_id}@{core_revision}:{name}",
            ),
        )

    def to_tool_result(self) -> ToolResult:
        return self.result


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ResolvedEffectEntry:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    source: str
    core_id: str
    core_revision: str
    adapter_key: str
    provenance: str
    _adapter: Any = field(repr=False, compare=False)
    slot_path: str | None = None
    risk: str = "low"
    capability: str | None = None
    approval_policy: str = "auto"
    model_output_policy: str = "content"
    display_policy: str = "summary"
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", _freeze_value(self.input_schema))

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=_thaw_value(self.input_schema),
        )

    def to_model_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "core_id": self.core_id,
            "core_revision": self.core_revision,
            "provenance": self.provenance,
            "slot_path": self.slot_path,
            "risk": self.risk,
            "capability": self.capability,
            "approval_policy": self.approval_policy,
            "model_output_policy": self.model_output_policy,
            "display_policy": self.display_policy,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class EffectRequest:
    entry: ResolvedEffectEntry
    name: str
    arguments: Mapping[str, Any]
    call_id: str
    origin: EffectOrigin
    catalog: ResolvedEffectCatalog

    def __post_init__(self) -> None:
        if self.entry.name != self.name:
            raise ValueError(
                "resolved effect entry does not match tool call: "
                f"{self.entry.name} != {self.name}"
            )
        if self.origin not in {"model", "host", "authored"}:
            raise ValueError(f"unsupported effect origin: {self.origin}")
        if (
            self.catalog.entry_for(self.name) is not self.entry
        ):
            raise ValueError(
                "resolved effect entry is not owned by resolved effect catalog: "
                f"{self.entry.provenance}"
            )
        object.__setattr__(self, "arguments", _freeze_value(self.arguments))

    @classmethod
    def from_call(
        cls,
        *,
        entry: ResolvedEffectEntry,
        call: ToolCall,
        catalog: ResolvedEffectCatalog,
        origin: EffectOrigin,
    ) -> EffectRequest:
        return cls(
            entry=entry,
            name=call.name,
            arguments=call.arguments,
            call_id=call.id,
            origin=origin,
            catalog=catalog,
        )

    def to_tool_call(self) -> ToolCall:
        return ToolCall(
            name=self.name,
            arguments=_thaw_value(self.arguments),
            id=self.call_id,
        )


@dataclass(frozen=True, slots=True)
class ResolvedEffectCatalog:
    core_id: str
    core_revision: str
    entries: tuple[ResolvedEffectEntry, ...]

    def __post_init__(self) -> None:
        seen: dict[str, ResolvedEffectEntry] = {}
        for entry in self.entries:
            if entry.core_id != self.core_id or entry.core_revision != self.core_revision:
                raise ValueError(
                    "resolved effect entry does not match catalog core snapshot: "
                    f"{entry.provenance}"
                )
            prior = seen.get(entry.name)
            if prior is not None:
                raise ToolRegistryCollisionError(
                    f"tool name collision: {entry.name}; "
                    f"{prior.provenance} conflicts with {entry.provenance}; "
                    "rename the authored or MCP tool"
                )
            seen[entry.name] = entry

    def definitions(self) -> list[ToolDefinition]:
        return [entry.to_definition() for entry in self.entries]

    def entry_for(self, name: str) -> ResolvedEffectEntry | None:
        return next((entry for entry in self.entries if entry.name == name), None)

    def request_for(
        self,
        call: ToolCall,
        *,
        origin: EffectOrigin = "model",
    ) -> EffectRequest | None:
        entry = self.entry_for(call.name)
        return (
            EffectRequest.from_call(
                entry=entry,
                call=call,
                catalog=self,
                origin=origin,
            )
            if entry is not None
            else None
        )

    def filtered(self, predicate: Any) -> ResolvedEffectCatalog:
        return ResolvedEffectCatalog(
            core_id=self.core_id,
            core_revision=self.core_revision,
            entries=tuple(entry for entry in self.entries if predicate(entry)),
        )
