from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from demiurge.providers import ToolDefinition
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
            "Read a text file inside the configured workspace. Use this instead of terminal cat/head/tail. "
            "Use offset and limit for large files."
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
            "Search workspace files by content or filename. Use target='content' for text matches, "
            "target='name' for file discovery, or target='both' when either is useful."
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
                "tool_policy": {"type": "object"},
                "max_depth": {"type": "integer"},
            },
            required=["goal"],
        ),
    ),
    "task_status": ToolDefinition(
        name="task_status",
        description="Inspect a delegated task or runtime control-plane task.",
        input_schema=_schema(
            {
                "task_id": {"type": "string"},
                "view": {"type": "string", "enum": ["model", "operator", "debug"], "default": "model"},
            },
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
    "session_search": {"risk": "low", "approval_policy": "auto", "model_output_policy": "current_turn"},
    "schedule_manage": {"risk": "high", "capability": "schedule.manage", "approval_policy": "prompt"},
    "tools_list": {"risk": "low", "approval_policy": "auto", "model_output_policy": "current_turn"},
}


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
APPROVAL_ORDER = {"auto": 0, "prompt": 1, "deny": 2}


@dataclass(slots=True)
class ToolRegistryEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    source: str
    slot_path: str | None = None
    risk: str = "low"
    capability: str | None = None
    approval_policy: str = "auto"
    model_output_policy: str = "content"
    display_policy: str = "summary"
    enabled: bool = True

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    def to_model_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "slot_path": self.slot_path,
            "risk": self.risk,
            "capability": self.capability,
            "approval_policy": self.approval_policy,
            "model_output_policy": self.model_output_policy,
            "display_policy": self.display_policy,
            "enabled": self.enabled,
        }
