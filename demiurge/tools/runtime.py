from __future__ import annotations

import asyncio
import codecs
import difflib
import fnmatch
import hashlib
import inspect
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from demiurge.runtime.tasks import (
    RuntimeTaskConflictError,
    RuntimeTaskContext,
    RuntimeTaskKindError,
    RuntimeTaskOutcome,
    RuntimeTaskWorker,
)
from demiurge.mcp import McpRuntime, McpToolInfo
from demiurge.security.approval import ApprovalRequest, ApprovalRuntime
from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade
from demiurge.security.command_guard import CommandGuardDecision, review_command
from demiurge.core import ApprovalInfo, CoreLoadError, CoreLoader, LoadedCore, SlotDefinition, ToolMetadataInfo, load_slot_callable
from demiurge.core_repository import CoreRepositoryError
from demiurge.providers import ToolCall, ToolDefinition
from demiurge.sdk import ToolContext, ToolResult, TurnContext
from demiurge.schedule_management import ScheduleManagementError, ScheduleManager
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone
from demiurge.runtime.session import SessionRuntime
from demiurge.storage import VersionStore
from demiurge.tools.registry import (
    APPROVAL_ORDER,
    BUILTIN_TOOL_DEFINITIONS,
    BUILTIN_TOOL_METADATA,
    RISK_ORDER,
    ToolRegistryEntry,
)
from demiurge.util import read_json, require_relative_path, write_json
from demiurge.security.workspace import (
    DEFAULT_READ_LIMIT_CHARS,
    DEFAULT_TOOL_OUTPUT_LIMIT_CHARS,
    WorkspaceScope,
    WorkspaceScopeError,
    truncate_text,
)


_AUTHORED_PREVIEW_MAX_CHARS = 2048
_AUTHORED_PREVIEW_MAX_ITEMS = 20
_AUTHORED_PREVIEW_MAX_DEPTH = 4
_AUTHORED_PREVIEW_MAX_STRING = 256
_SENSITIVE_ARGUMENT_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)


def _safe_authored_arguments_preview(arguments: Mapping[str, Any]) -> dict[str, Any]:
    preview = _sanitize_authored_preview_value(dict(arguments), depth=0)
    if not isinstance(preview, dict):
        return {"value": preview}
    serialized = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    if len(serialized) <= _AUTHORED_PREVIEW_MAX_CHARS:
        return preview
    bounded: dict[str, Any] = {}
    for key, value in preview.items():
        candidate = {**bounded, key: value, "_truncated": True}
        if len(json.dumps(candidate, ensure_ascii=False, sort_keys=True)) <= _AUTHORED_PREVIEW_MAX_CHARS:
            bounded[key] = value
        else:
            bounded[key] = "<truncated>"
    bounded["_truncated"] = True
    while len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)) > _AUTHORED_PREVIEW_MAX_CHARS:
        removable = next((key for key in reversed(bounded) if key != "_truncated"), None)
        if removable is None:
            break
        bounded.pop(removable)
    return bounded


def _sanitize_authored_preview_value(value: Any, *, depth: int) -> Any:
    if depth >= _AUTHORED_PREVIEW_MAX_DEPTH:
        return "<truncated>"
    if isinstance(value, Mapping):
        preview: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, child in items[:_AUTHORED_PREVIEW_MAX_ITEMS]:
            key = truncate_text(str(raw_key), limit=80)
            if _is_sensitive_argument_key(key):
                preview[key] = "<redacted>"
            else:
                preview[key] = _sanitize_authored_preview_value(child, depth=depth + 1)
        if len(items) > _AUTHORED_PREVIEW_MAX_ITEMS:
            preview["_truncated_items"] = len(items) - _AUTHORED_PREVIEW_MAX_ITEMS
        return preview
    if isinstance(value, (list, tuple)):
        items = [
            _sanitize_authored_preview_value(item, depth=depth + 1)
            for item in value[:_AUTHORED_PREVIEW_MAX_ITEMS]
        ]
        if len(value) > _AUTHORED_PREVIEW_MAX_ITEMS:
            items.append(f"<truncated {len(value) - _AUTHORED_PREVIEW_MAX_ITEMS} items>")
        return items
    if isinstance(value, str):
        return truncate_text(value, limit=_AUTHORED_PREVIEW_MAX_STRING)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return truncate_text(repr(value), limit=_AUTHORED_PREVIEW_MAX_STRING)


def _is_sensitive_argument_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_ARGUMENT_KEY_PARTS)


EventEmitter = Callable[..., dict[str, Any]]
SKILL_SUPPORT_DIRS = frozenset({"references", "templates", "scripts", "assets"})
_COMMAND_SPECIFIC_APPROVAL_RULES = frozenset(
    {
        "command-substitution",
        "complex-shell",
        "pipeline",
        "process-substitution",
        "script-eval",
        "shell-eval",
        "shell-expansion",
        "shell-redirection",
        "unknown-command",
    }
)


@dataclass(slots=True)
class SkillTarget:
    name: str
    skill_dir: Path
    skill_file: Path
    packaged: bool
    exists: bool


def _terminal_execution_command(command: str) -> str:
    if os.name != "nt":
        return command
    return _windows_posix_compat_command(command) or command


def _terminal_approval_cache_key(
    command: str,
    decision: CommandGuardDecision,
    *,
    cwd: str,
    env_overlay: Mapping[str, str],
    execution_options: Mapping[str, Any],
) -> str:
    base = f"terminal:terminal.exec:{decision.rule_key}"
    if decision.rule_key not in _COMMAND_SPECIFIC_APPROVAL_RULES:
        return base
    fingerprint = json.dumps(
        {
            "command": command,
            "cwd": cwd,
            "env": sorted((str(key), str(value)) for key, value in env_overlay.items()),
            "execution_options": dict(execution_options),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"{base}:{digest}"


def _windows_posix_compat_command(command: str) -> str | None:
    if any(token in command for token in ("\n", "\r", "|", "&&", ";", ">", "<")):
        return None
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    name = parts[0]
    args = parts[1:]
    if name == "printf" and args:
        return _windows_printf_command(args)
    if name == "sleep" and len(args) == 1:
        try:
            seconds = float(args[0])
        except ValueError:
            return None
        if seconds < 0:
            return None
        return _python_shell_command("import sys, time; time.sleep(float(sys.argv[1]))", [str(seconds)])
    if name == "pwd" and not args:
        return _python_shell_command("from pathlib import Path; print(Path.cwd())", [])
    if name == "rm":
        return _windows_rm_command(args)
    if name == "true" and not args:
        return _python_shell_command("import sys; sys.exit(0)", [])
    if name == "false" and not args:
        return _python_shell_command("import sys; sys.exit(1)", [])
    return None


def _windows_rm_command(args: list[str]) -> str | None:
    force = False
    targets: list[str] = []
    for arg in args:
        if arg == "-f":
            force = True
            continue
        if arg.startswith("-"):
            return None
        targets.append(arg)
    if not targets:
        return None
    code = (
        "import os, sys; "
        "force = sys.argv[1] == '1'; "
        "missing = []; "
        "[os.remove(path) if os.path.exists(path) else missing.append(path) for path in sys.argv[2:]]; "
        "sys.exit(0 if force or not missing else 1)"
    )
    return _python_shell_command(code, ["1" if force else "0", *targets])


def _windows_printf_command(args: list[str]) -> str:
    code = (
        "import sys; "
        "from demiurge.tools.runtime import _format_windows_printf; "
        "sys.stdout.write(_format_windows_printf(sys.argv[1], sys.argv[2:]))"
    )
    return _python_shell_command(code, args)


def _format_windows_printf(format_text: str, values: list[str]) -> str:
    decoded_format = codecs.decode(format_text, "unicode_escape")
    return decoded_format % tuple(values) if values else decoded_format


def _python_shell_command(code: str, args: list[str]) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", code, *args])


class ToolRuntime:
    def __init__(
        self,
        version_store: VersionStore,
        evolution_runtime: Any | None = None,
        *,
        workspace: WorkspaceScope | None = None,
        approval_runtime: ApprovalRuntime | None = None,
        global_approval: ApprovalInfo | None = None,
        mcp_runtime: McpRuntime | None = None,
        runtime_timezone: RuntimeTimezone | None = None,
        task_worker: RuntimeTaskWorker | None = None,
        session_runtime: SessionRuntime | None = None,
    ):
        self.version_store = version_store
        self.evolution_runtime = evolution_runtime
        self.workspace = workspace or WorkspaceScope(Path.cwd())
        self.approval_runtime = approval_runtime or ApprovalRuntime()
        self.global_approval = global_approval or ApprovalInfo()
        self.mcp_runtime = mcp_runtime
        self.runtime_timezone = runtime_timezone or resolve_runtime_timezone()
        if task_worker is None:
            raise ValueError("ToolRuntime requires a RuntimeControlPlane-backed RuntimeTaskWorker")
        self.task_worker = task_worker
        self.session_runtime = session_runtime

    async def prepare_for_turn(
        self,
        core: LoadedCore,
        turn: TurnContext,
        *,
        emit_event: EventEmitter | None = None,
    ) -> None:
        if self.mcp_runtime is None or not core.mcp_servers:
            return
        await self.mcp_runtime.prepare_for_turn(core, turn, emit_event=emit_event)

    async def close(self) -> None:
        if self.mcp_runtime is not None:
            await self.mcp_runtime.close()

    def registry_for(self, core: LoadedCore, *, turn: TurnContext | None = None) -> list[ToolRegistryEntry]:
        entries: list[ToolRegistryEntry] = []
        for name in core.builtin_tool_names:
            definition = BUILTIN_TOOL_DEFINITIONS.get(name)
            if not definition:
                continue
            metadata = BUILTIN_TOOL_METADATA.get(name, {})
            entry = ToolRegistryEntry(
                name=definition.name,
                description=definition.description,
                input_schema=definition.input_schema,
                source="builtin",
                risk=self._normalize_risk(str(metadata.get("risk") or "low")),
                capability=metadata.get("capability"),
                approval_policy=self._normalize_approval_policy(str(metadata.get("approval_policy") or "auto")),
                model_output_policy=str(metadata.get("model_output_policy") or "content"),
                display_policy=str(metadata.get("display_policy") or "summary"),
            )
            configured = core.manifest.tools.metadata.get(name)
            if configured:
                self._apply_metadata(entry, configured, allow_lower_risk=False, allow_weaker_approval=False)
            if entry.enabled:
                entries.append(entry)
        for slot in core.tool_slots:
            entry = ToolRegistryEntry(
                name=slot.slot_id,
                description=slot.description,
                input_schema=slot.input_schema or {"type": "object", "properties": {}},
                source="authored",
                slot_path=slot.relative_path,
                risk=self._normalize_risk(str(slot.manifest.get("risk") or "medium")),
                capability=slot.manifest.get("capability"),
                approval_policy=self._normalize_approval_policy(str(slot.manifest.get("approval_policy") or "prompt")),
                model_output_policy=str(slot.manifest.get("model_output_policy") or "content"),
                display_policy=str(slot.manifest.get("display_policy") or "summary"),
                enabled=bool(slot.manifest.get("enabled", True)),
            )
            configured = core.manifest.tools.metadata.get(slot.slot_id)
            if configured:
                self._apply_metadata(entry, configured, allow_lower_risk=True, allow_weaker_approval=True)
            if entry.enabled:
                entries.append(entry)
        if self.mcp_runtime is not None:
            for tool in self.mcp_runtime.entries_for(core):
                entry = ToolRegistryEntry(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema or {"type": "object", "properties": {}},
                    source="mcp",
                    slot_path=tool.relative_path,
                    risk=self._normalize_risk(tool.risk),
                    capability=tool.capability,
                    approval_policy=self._normalize_approval_policy(tool.approval_policy),
                    model_output_policy="content",
                    display_policy="summary",
                    enabled=True,
                )
                configured = core.manifest.tools.metadata.get(tool.name)
                if configured:
                    self._apply_metadata(entry, configured, allow_lower_risk=True, allow_weaker_approval=True)
                if entry.enabled:
                    entries.append(entry)
        return [entry for entry in entries if self._tool_policy_allows(entry, self._tool_policy(turn))]

    def _apply_metadata(
        self,
        entry: ToolRegistryEntry,
        metadata: ToolMetadataInfo,
        *,
        allow_lower_risk: bool,
        allow_weaker_approval: bool,
    ) -> None:
        if metadata.risk:
            risk = self._normalize_risk(metadata.risk)
            if allow_lower_risk or RISK_ORDER[risk] >= RISK_ORDER[entry.risk]:
                entry.risk = risk
        if metadata.capability:
            entry.capability = metadata.capability
        if metadata.approval_policy:
            policy = self._normalize_approval_policy(metadata.approval_policy)
            if allow_weaker_approval or APPROVAL_ORDER[policy] >= APPROVAL_ORDER[entry.approval_policy]:
                entry.approval_policy = policy
        if metadata.model_output_policy:
            entry.model_output_policy = metadata.model_output_policy
        if metadata.display_policy:
            entry.display_policy = metadata.display_policy
        if metadata.enabled is not None:
            entry.enabled = metadata.enabled

    def definitions_for(self, core: LoadedCore, *, turn: TurnContext | None = None) -> list[ToolDefinition]:
        return [entry.to_definition() for entry in self.registry_for(core, turn=turn)]

    async def execute(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None = None,
        output_factory: Callable[[SlotDefinition], Any] | None = None,
    ) -> ToolResult:
        try:
            visible_tools = {
                entry.name: entry
                for entry in self.registry_for(core, turn=turn)
            }
            if call.name in BUILTIN_TOOL_DEFINITIONS:
                if call.name not in visible_tools:
                    return ToolResult(content=f"builtin tool is not allowed: {call.name}", is_error=True)
                return await self._execute_builtin(
                    call,
                    core=core,
                    turn=turn,
                    capability=capability,
                    emit_event=emit_event,
                )
            slot = next((item for item in core.tool_slots if item.slot_id == call.name), None)
            if slot:
                entry = visible_tools.get(call.name)
                if entry is None or entry.source != "authored":
                    return ToolResult(content=f"authored tool is not allowed: {call.name}", is_error=True)
                if entry.capability:
                    capability._require_registry_capability(
                        entry.capability,
                        slot_path=slot.relative_path,
                    )
                approval_denial = await self._approval_for_authored(
                    entry,
                    slot,
                    call,
                    core=core,
                    turn=turn,
                    emit_event=emit_event,
                )
                if approval_denial is not None:
                    return approval_denial
                return await self._execute_authored(
                    slot,
                    call,
                    core=core,
                    turn=turn,
                    capability=capability,
                    output_factory=output_factory,
                )
            mcp_tool = self.mcp_runtime.tool_info(call.name) if self.mcp_runtime is not None else None
            if mcp_tool is not None:
                if call.name not in visible_tools:
                    return ToolResult(content=f"MCP tool is not allowed: {call.name}", is_error=True)
                return await self._execute_mcp(
                    mcp_tool,
                    call,
                    core=core,
                    turn=turn,
                    capability=capability,
                    emit_event=emit_event,
                )
            else:
                return ToolResult(content=f"tool not found: {call.name}", is_error=True)
        except (CapabilityDenied, WorkspaceScopeError, ValueError, OSError) as exc:
            return ToolResult(content=str(exc), is_error=True, data={"executionStarted": False})

    def _tool_policy(self, turn: TurnContext | None) -> Mapping[str, Any]:
        if turn is None or not isinstance(turn.metadata, Mapping):
            return {}
        policy = turn.metadata.get("tool_policy")
        return policy if isinstance(policy, Mapping) else {}

    def _tool_policy_allows(self, entry: ToolRegistryEntry, policy: Mapping[str, Any]) -> bool:
        if not policy:
            return True
        deny = self._policy_patterns(policy.get("deny"))
        if any(self._policy_pattern_matches(entry, pattern) for pattern in deny):
            return False
        allow_exact = self._policy_exact_ids(policy.get("allow_exact"))
        if allow_exact is not None and entry.name not in allow_exact:
            return False
        allow = self._policy_patterns(policy.get("allow"))
        if allow and not any(self._policy_pattern_matches(entry, pattern) for pattern in allow):
            return False
        risk_ceiling = policy.get("risk_ceiling") or policy.get("max_risk") or policy.get("risk")
        if risk_ceiling:
            ceiling = self._normalize_risk(str(risk_ceiling))
            if RISK_ORDER[entry.risk] > RISK_ORDER[ceiling]:
                return False
        return True

    def _policy_patterns(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list | tuple | set):
            return [str(item) for item in value if str(item).strip()]
        return []

    def _policy_exact_ids(self, value: Any) -> set[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return {text} if text else set()
        if isinstance(value, list | tuple | set):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()

    def _policy_pattern_matches(self, entry: ToolRegistryEntry, pattern: str) -> bool:
        normalized = pattern.strip()
        if not normalized:
            return False
        if fnmatch.fnmatch(entry.name, normalized):
            return True
        capability = entry.capability or ""
        if capability and (capability == normalized or capability.startswith(normalized.rstrip("*"))):
            return True
        return False

    async def _execute_builtin(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None = None,
    ) -> ToolResult:
        if call.name == "rollback_core":
            capability.require("tool.call:rollback_core")
            try:
                pointer = self.version_store.rollback(
                    core.core_id,
                    target=str(call.arguments.get("target") or "previous"),
                    reason=str(call.arguments.get("reason") or "rollback_core"),
                )
            except CoreRepositoryError as exc:
                return ToolResult(
                    content=str(exc),
                    data={"error": str(exc)},
                    is_error=True,
                    model_output=str(exc),
                )
            payload = asdict(pointer)
            return ToolResult(
                content=f"rollback committed: {pointer.active_revision[:12]} (takes effect next turn)",
                data=payload,
                model_output=json.dumps(payload, ensure_ascii=False),
            )
        if call.name == "evolve_core":
            capability.require("tool.call:evolve_core")
            if self.evolution_runtime is None:
                return ToolResult(content="evolution runtime is not configured", is_error=True)
            action = str(call.arguments.get("action") or "start").strip().lower()
            background = bool(call.arguments.get("background", False))
            notify_on_complete = bool(call.arguments.get("notify_on_complete", True))
            goal = str(call.arguments.get("goal") or "")
            run_id = str(call.arguments.get("run_id") or "").strip()
            reason = str(call.arguments.get("reason") or goal or f"evolve {action}").strip()
            if action == "start":
                if not goal.strip():
                    return ToolResult(content="goal is required for evolve_core start", is_error=True)
                if background:
                    return self._start_evolve_task(
                        core=core,
                        turn=turn,
                        goal=goal,
                        notify_on_complete=notify_on_complete,
                    )
                result = await self.evolution_runtime.start(
                    target_core_id=core.core_id,
                    goal=goal,
                    source_turn_id=turn.turn_id,
                )
                payload = asdict(result)
                return ToolResult(content=result.summary, data=payload, model_output=json.dumps(payload, ensure_ascii=False))
            if action == "review":
                if not run_id:
                    return ToolResult(content="run_id is required for evolve_core review", is_error=True)
                result = await self.evolution_runtime.review(run_id, target_core_id=core.core_id, goal=reason)
                payload = asdict(result)
                content = f"evolve review {run_id}: {'passed' if result.passed else 'failed'}"
                return ToolResult(content=content, data=payload, is_error=not result.passed, model_output=json.dumps(payload, ensure_ascii=False))
            if action == "promote":
                if not run_id:
                    return ToolResult(content="run_id is required for evolve_core promote", is_error=True)
                try:
                    result = await self.evolution_runtime.promote(run_id, target_core_id=core.core_id, reason=reason)
                except CoreRepositoryError as exc:
                    return ToolResult(content=str(exc), data={"error": str(exc)}, is_error=True, model_output=str(exc))
                payload = asdict(result)
                return ToolResult(content=result.summary, data=payload, is_error=not result.promoted, model_output=json.dumps(payload, ensure_ascii=False))
            if action == "discard":
                if not run_id:
                    return ToolResult(content="run_id is required for evolve_core discard", is_error=True)
                payload = self.evolution_runtime.discard(run_id)
                return ToolResult(content=f"discarded evolve run {run_id}", data=payload, model_output=json.dumps(payload, ensure_ascii=False))
            return ToolResult(content=f"unsupported evolve_core action: {action}", is_error=True)
        if call.name == "read_file":
            return await self._read_file(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "search_files":
            return await self._search_files(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "write_file":
            return await self._write_file(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "patch":
            return await self._patch(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "terminal":
            return await self._terminal(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "task_list":
            return self._task_list(call, turn=turn, capability=capability)
        if call.name in {"delegate_task", "task_status", "task_control", "yield_until"}:
            return ToolResult(content=f"delegation tool requires the active turn runtime: {call.name}", is_error=True)
        if call.name == "skills_list":
            return self._skills_list(call, core=core)
        if call.name == "skill_view":
            return self._skill_view(call, core=core)
        if call.name == "skill_manage":
            return await self._skill_manage(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "todo":
            return self._todo(call, turn=turn)
        if call.name == "clarify":
            return self._clarify(call)
        if call.name == "web_extract":
            return await self._web_extract(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "session_search":
            return self._session_search(call)
        if call.name == "schedule_manage":
            return await self._schedule_manage(call, core=core, turn=turn, capability=capability, emit_event=emit_event)
        if call.name == "tools_list":
            return self._tools_list(core)
        return ToolResult(content=f"unsupported builtin tool: {call.name}", is_error=True)

    async def _execute_mcp(
        self,
        tool: McpToolInfo,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        if self.mcp_runtime is None:
            return ToolResult(content="MCP runtime is not configured", is_error=True, data={"executionStarted": False})
        entry = next((item for item in self.registry_for(core) if item.name == call.name), None)
        capability_name = (entry.capability if entry is not None else None) or tool.capability
        risk = (entry.risk if entry is not None else None) or tool.risk
        capability.require(capability_name)
        denied = await self._approval_for_mcp(
            call,
            core=core,
            turn=turn,
            tool=tool,
            capability_name=capability_name,
            risk=risk,
            emit_event=emit_event,
        )
        if denied:
            return denied
        return await self.mcp_runtime.call_tool(tool, call.arguments)

    async def _read_file(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("fs.read")
        target = self.workspace.resolve_path(
            str(call.arguments.get("path") or ""),
            operation="read",
            allow_outside_read=True,
        )
        needs_prompt = target.outside or target.sensitive
        denied = await self._approval_for_path(
            call,
            core=core,
            turn=turn,
            capability_name="fs.read",
            action="read",
            target=target.relative,
            risk="low" if not needs_prompt else "high",
            summary=f"Read file {target.relative}",
            auto_approve=not needs_prompt,
            emit_event=emit_event,
        )
        if denied:
            return denied
        if not target.path.exists():
            return ToolResult(content=f"path does not exist: {target.relative}", is_error=True)
        if not target.path.is_file():
            return ToolResult(content=f"path is not a file: {target.relative}", is_error=True)
        offset = max(0, int(call.arguments.get("offset") or 0))
        limit = self._positive_int(
            call.arguments.get("limit"),
            default=DEFAULT_READ_LIMIT_CHARS,
            maximum=DEFAULT_READ_LIMIT_CHARS,
        )
        text = target.path.read_text(encoding="utf-8", errors="replace")
        end = min(len(text), offset + limit)
        chunk = text[offset:end]
        if end < len(text):
            chunk = f"{chunk}\n...[truncated {len(text) - end} chars]"
        return ToolResult(
            content=chunk,
            data={"path": target.relative, "offset": offset, "end": end, "total_chars": len(text)},
        )

    async def _search_files(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("fs.read")
        query = str(call.arguments.get("query") or "")
        target_kind = str(call.arguments.get("target") or "content").strip().lower()
        if target_kind not in {"content", "name", "both"}:
            return ToolResult(content=f"unsupported search target: {target_kind}", is_error=True)
        if not query and target_kind in {"content", "both"}:
            return ToolResult(content="query is required for content search", is_error=True)
        target = self.workspace.resolve_path(
            call.arguments.get("path") or ".",
            operation="read",
            allow_outside_read=True,
        )
        include_sensitive = bool(call.arguments.get("include_sensitive", False))
        needs_prompt = target.outside or target.sensitive or (
            include_sensitive and self.workspace.contains_sensitive_children(target.path, operation="read")
        )
        denied = await self._approval_for_path(
            call,
            core=core,
            turn=turn,
            capability_name="fs.read",
            action="search",
            target=target.relative,
            risk="low" if not needs_prompt else "high",
            summary=f"Search files under {target.relative}",
            auto_approve=not needs_prompt,
            emit_event=emit_event,
        )
        if denied:
            return denied
        if not target.path.exists():
            return ToolResult(content=f"path does not exist: {target.relative}", is_error=True)
        pattern = str(call.arguments.get("pattern") or "*")
        case_sensitive = bool(call.arguments.get("case_sensitive", True))
        needle = query if case_sensitive else query.lower()
        max_results = self._positive_int(call.arguments.get("max_results"), default=50, maximum=200)
        matches: list[dict[str, Any]] = []
        lines: list[str] = []
        files = (
            [target.path]
            if target.path.is_file()
            else sorted(target.path.rglob("*"), key=lambda item: item.as_posix())
        )
        for path in files:
            resolved = path.resolve(strict=False)
            try:
                self.workspace.require_within_workspace(resolved)
            except WorkspaceScopeError:
                if not target.outside:
                    continue
                try:
                    resolved.relative_to(target.path)
                except ValueError:
                    continue
            if not resolved.is_file():
                continue
            if self.workspace.is_sensitive_path(resolved, operation="read") and not include_sensitive:
                continue
            rel = self.workspace.relative_display(resolved)
            if not fnmatch.fnmatch(rel, pattern) and not fnmatch.fnmatch(resolved.name, pattern):
                continue
            if target_kind in {"name", "both"}:
                haystack_name = rel if case_sensitive else rel.lower()
                if not query or needle in haystack_name:
                    display = rel + ("/" if resolved.is_dir() else "")
                    lines.append(display)
                    matches.append({"type": "name", "path": rel, "is_dir": resolved.is_dir()})
                    if len(matches) >= max_results:
                        break
            if resolved.is_file() and target_kind in {"content", "both"} and len(matches) < max_results:
                try:
                    text = resolved.read_text(encoding="utf-8", errors="replace")[:200_000]
                except OSError:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    haystack = line if case_sensitive else line.lower()
                    if needle in haystack:
                        display = f"{rel}:{line_no}: {line[:300]}"
                        lines.append(display)
                        matches.append({"type": "content", "path": rel, "line": line_no, "text": line})
                        if len(matches) >= max_results:
                            break
            if len(matches) >= max_results:
                break
        truncated = len(matches) >= max_results
        return ToolResult(
            content="\n".join(lines) or "(no matches)",
            data={"matches": matches, "truncated": truncated},
            model_output="\n".join(lines) or "(no matches)",
        )

    async def _write_file(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("fs.write")
        target = self.workspace.resolve_path(str(call.arguments.get("path") or ""), operation="write")
        denied = await self._approval_for_path(
            call,
            core=core,
            turn=turn,
            capability_name="fs.write",
            action="write",
            target=target.relative,
            risk="high",
            summary=f"Write file {target.relative}",
            auto_approve=False,
            emit_event=emit_event,
        )
        if denied:
            return denied
        content = str(call.arguments.get("content") or "")
        if bool(call.arguments.get("create_parent_dirs", True)):
            target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(content, encoding="utf-8")
        return ToolResult(
            content=f"wrote {len(content)} chars to {target.relative}",
            data={"path": target.relative, "chars": len(content), "executionStarted": True},
        )

    async def _patch(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("fs.write")
        target = self.workspace.resolve_path(str(call.arguments.get("path") or ""), operation="write")
        denied = await self._approval_for_path(
            call,
            core=core,
            turn=turn,
            capability_name="fs.write",
            action="patch",
            target=target.relative,
            risk="high",
            summary=f"Patch file {target.relative}",
            auto_approve=False,
            emit_event=emit_event,
        )
        if denied:
            return denied
        old = str(call.arguments.get("old") or "")
        new = str(call.arguments.get("new") or "")
        if not old:
            return ToolResult(content="old text is required", is_error=True)
        text = target.path.read_text(encoding="utf-8", errors="replace")
        count = int(call.arguments.get("count") if call.arguments.get("count") is not None else -1)
        if old not in text:
            return ToolResult(content=f"old text not found in {target.relative}", is_error=True)
        patched = text.replace(old, new, count if count >= 0 else -1)
        target.path.write_text(patched, encoding="utf-8")
        diff = "\n".join(
            difflib.unified_diff(
                text.splitlines(),
                patched.splitlines(),
                fromfile=f"a/{target.relative}",
                tofile=f"b/{target.relative}",
                lineterm="",
            )
        )
        return ToolResult(
            content=diff or f"patched {target.relative}",
            data={"path": target.relative, "diff": diff, "executionStarted": True},
            model_output=diff or f"patched {target.relative}",
        )

    async def _terminal(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("terminal.exec")
        command = str(call.arguments.get("command") or "").strip()
        if not command:
            content = "command is required"
            cwd_display = str(call.arguments.get("cwd") or ".")
            return ToolResult(
                content=content,
                is_error=True,
                display_output=self._format_command_display(command, cwd_display, content),
            )
        cwd = self.workspace.resolve_path(call.arguments.get("cwd") or ".", operation="write")
        env_overlay = call.arguments.get("env") or {}
        if not isinstance(env_overlay, Mapping):
            content = "env must be an object"
            return ToolResult(
                content=content,
                is_error=True,
                display_output=self._format_command_display(command, cwd.relative, content),
            )
        normalized_env_overlay = {str(key): str(value) for key, value in env_overlay.items()}
        timeout = self._positive_int(call.arguments.get("timeout_seconds"), default=30, maximum=120)
        background = bool(call.arguments.get("background", False))
        command_guard = review_command(command)
        if command_guard.action == "block":
            content = f"terminal command blocked: {command_guard.reason}"
            return ToolResult(
                content=content,
                data={"executionStarted": False, "command_guard": asdict(command_guard)},
                is_error=True,
                display_output=self._format_command_display(command, cwd.relative, content),
            )
        denied = await self._approval_for_command(
            call,
            core=core,
            turn=turn,
            cwd=cwd.relative,
            command=command,
            env_keys=sorted(normalized_env_overlay),
            env_overlay=normalized_env_overlay,
            execution_options={"background": background, "timeout_seconds": timeout},
            command_guard=command_guard,
            emit_event=emit_event,
        )
        if denied:
            return denied
        env = os.environ.copy()
        env.update(normalized_env_overlay)
        env = self.runtime_timezone.apply_subprocess_env(env)
        execution_command = _terminal_execution_command(command)
        if background:
            return self._start_background_task(
                command=command,
                execution_command=execution_command,
                cwd=cwd,
                env=env,
                owner_session_id=turn.session_id,
                owner_turn_id=turn.turn_id,
                notify_on_complete=bool(call.arguments.get("notify_on_complete", True)),
            )
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                execution_command,
                cwd=cwd.path,
                env=env,
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            content = self._format_command_result(completed)
            return ToolResult(
                content=content,
                is_error=completed.returncode != 0,
                data={
                    "executionStarted": True,
                    "exit_code": completed.returncode,
                    "cwd": cwd.relative,
                    "timed_out": False,
                },
                display_output=self._format_command_display(command, cwd.relative, content),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            content = self._format_command_output(124, stdout, stderr, timed_out=True)
            return ToolResult(
                content=content,
                is_error=True,
                data={"executionStarted": True, "exit_code": 124, "cwd": cwd.relative, "timed_out": True},
                display_output=self._format_command_display(command, cwd.relative, content),
            )

    def _start_background_task(
        self,
        *,
        command: str,
        execution_command: str,
        cwd: Any,
        env: Mapping[str, str],
        owner_session_id: str,
        owner_turn_id: str,
        notify_on_complete: bool,
    ) -> ToolResult:
        async def terminal_task(ctx: RuntimeTaskContext) -> RuntimeTaskOutcome:
            process = await asyncio.create_subprocess_shell(
                execution_command,
                cwd=cwd.path,
                env=dict(env),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            ctx.update_metadata(
                {
                    "pid": process.pid,
                    "command": command,
                    "cwd": cwd.relative,
                    "returncode": None,
                }
            )

            async def cancel_process() -> None:
                if process.returncode is not None:
                    return
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

            ctx.set_cancel_callback(cancel_process)
            await self._capture_process_output(process, ctx)
            returncode = process.returncode
            ctx.update_metadata({"returncode": returncode})
            summary = f"terminal command exited {returncode}"
            ctx.set_summary(summary)
            if returncode != 0 and self.task_worker.get(ctx.task_id).status != "cancelled":
                raise RuntimeError(summary)
            return RuntimeTaskOutcome(summary=summary, metadata={"returncode": returncode})

        try:
            record = self.task_worker.start_task(
                kind="terminal.exec",
                owner_session_id=owner_session_id,
                owner_turn_id=owner_turn_id,
                source_tool="terminal",
                task_factory=terminal_task,
                write_scope=f"terminal:{cwd.path}",
                notify_on_complete=notify_on_complete,
                metadata={"command": command, "cwd": cwd.relative},
            )
        except RuntimeTaskConflictError as exc:
            content = str(exc)
            return ToolResult(
                content=content,
                data={"executionStarted": False},
                is_error=True,
                display_output=self._format_command_display(command, cwd.relative, content),
            )
        payload = {"task_id": record.task_id}
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(
            content=content,
            data=payload,
            display_output=self._format_command_display(command, cwd.relative, content),
        )

    async def _capture_process_output(
        self,
        process: asyncio.subprocess.Process,
        ctx: RuntimeTaskContext,
    ) -> None:
        async def read_stream(stream: asyncio.StreamReader | None, label: str) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.readline()
                if not chunk:
                    break
                ctx.append_log(f"{label}: {chunk.decode('utf-8', errors='replace').rstrip()}")

        await asyncio.gather(read_stream(process.stdout, "stdout"), read_stream(process.stderr, "stderr"))
        await process.wait()

    def _start_evolve_task(
        self,
        *,
        core: LoadedCore,
        turn: TurnContext,
        goal: str,
        notify_on_complete: bool,
    ) -> ToolResult:
        assert self.evolution_runtime is not None

        async def run_evolve_task(ctx: RuntimeTaskContext) -> RuntimeTaskOutcome:
            result = await self.evolution_runtime.start(
                target_core_id=core.core_id,
                goal=goal,
                source_turn_id=turn.turn_id,
            )
            payload = asdict(result)
            ctx.update_metadata(payload)
            ctx.set_result_ref(result.report_path)
            if result.evolver.get("needs_user"):
                summary = f"evolve task needs user input for {core.core_id}"
                ctx.mark_blocked(summary, metadata=payload)
                return RuntimeTaskOutcome(summary=summary, result_ref=result.report_path, metadata=payload)
            return RuntimeTaskOutcome(summary=result.summary, result_ref=result.report_path, metadata=payload)

        try:
            record = self.task_worker.start_task(
                kind="evolver.run",
                owner_session_id=turn.session_id,
                owner_turn_id=turn.turn_id,
                source_tool="evolve_core",
                task_factory=run_evolve_task,
                write_scope=f"evolve:{core.core_id}",
                notify_on_complete=notify_on_complete,
                metadata={"target_core_id": core.core_id, "goal": goal},
            )
        except RuntimeTaskConflictError as exc:
            return ToolResult(content=str(exc), data={"executionStarted": False}, is_error=True)
        payload = {"task_id": record.task_id}
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def _task_list(self, call: ToolCall, *, turn: TurnContext, capability: CapabilityFacade) -> ToolResult:
        capability.require("task.control")
        kind = call.arguments.get("kind")
        include_completed = bool(call.arguments.get("include_completed", True))
        try:
            records = self.task_worker.list_tasks(
                owner_session_id=turn.session_id,
                kind=str(kind) if kind else None,
                include_completed=include_completed,
            )
        except RuntimeTaskKindError as exc:
            return ToolResult(content=str(exc), is_error=True)
        tasks = [record.to_payload(include_log=False) for record in records]
        payload = {"tasks": tasks}
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload, model_output=json.dumps(payload, ensure_ascii=False))

    def _skills_list(self, call: ToolCall, *, core: LoadedCore) -> ToolResult:
        category = str(call.arguments.get("category") or "").strip()
        skills = [skill for skill in core.skills if not category or skill.category == category]
        payload = {
            "success": True,
            "skills": [self._skill_metadata(skill) for skill in sorted(skills, key=lambda item: (item.category, item.name))],
            "categories": sorted({skill.category for skill in core.skills}),
            "count": len(skills),
            "hint": "Use skill_view(name) to load full content, or skill_view(name, file_path) for linked files.",
        }
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(content=content, data=payload, model_output=content)

    def _skill_view(self, call: ToolCall, *, core: LoadedCore) -> ToolResult:
        name = str(call.arguments.get("name") or "").strip()
        return self._skill_view_by_name(core, name=name, file_path=call.arguments.get("file_path"))

    async def _skill_manage(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("fs.write")
        action = str(call.arguments.get("action") or "").strip().lower()
        name = str(call.arguments.get("name") or "").strip()
        if action not in {"create", "update", "delete", "patch", "write_file", "remove_file"}:
            return ToolResult(content=f"unsupported skill_manage action: {action}", is_error=True)
        if not name:
            return ToolResult(content="name is required", is_error=True)

        skill_root = self._skill_root(core)
        target_or_error = self._skill_target(core, name, skill_root)
        if isinstance(target_or_error, ToolResult):
            return target_or_error
        target = target_or_error

        prepared = self._prepare_skill_manage_action(core, action, target, call.arguments)
        if isinstance(prepared, ToolResult):
            return prepared
        approval_target, mutation_root, mutate = prepared

        denied = await self._approval_for_skill_manage(
            call,
            core=core,
            turn=turn,
            action=action,
            target=approval_target.relative_to(core.root).as_posix(),
            emit_event=emit_event,
        )
        if denied:
            return denied

        return self._apply_skill_mutation(core=core, action=action, mutation_root=mutation_root, mutate=mutate)

    def _skill_root(self, core: LoadedCore) -> Path:
        surface_root = require_relative_path(core.root / core.manifest.runtime.surface_root, core.root)
        configured = core.manifest.slots.get("skills") or (surface_root / "skills").relative_to(core.root).as_posix()
        return require_relative_path(core.root / configured, core.root)

    def _skill_target(self, core: LoadedCore, name: str, skill_root: Path) -> SkillTarget | ToolResult:
        requested = Path(name)
        if (
            requested.is_absolute()
            or ".." in requested.parts
            or len(requested.parts) != 1
            or any(part.startswith(".") for part in requested.parts)
        ):
            return ToolResult(content="name must be a single non-hidden relative skill id", is_error=True)

        existing = core.skill_by_id(name)
        if existing is not None:
            skill_file = require_relative_path(existing.path, skill_root)
            return SkillTarget(
                name=name,
                skill_dir=skill_file.parent if existing.packaged else skill_file.parent,
                skill_file=skill_file,
                packaged=existing.packaged,
                exists=True,
            )

        packaged_file = require_relative_path(skill_root / requested.as_posix() / "SKILL.md", skill_root)
        if packaged_file.exists():
            return SkillTarget(
                name=name,
                skill_dir=packaged_file.parent,
                skill_file=packaged_file,
                packaged=True,
                exists=True,
            )

        single_file = require_relative_path(skill_root / f"{requested.as_posix()}.md", skill_root)
        if single_file.exists():
            return SkillTarget(
                name=name,
                skill_dir=single_file.parent,
                skill_file=single_file,
                packaged=False,
                exists=True,
            )

        return SkillTarget(
            name=name,
            skill_dir=packaged_file.parent,
            skill_file=packaged_file,
            packaged=True,
            exists=False,
        )

    def _prepare_skill_manage_action(
        self,
        core: LoadedCore,
        action: str,
        target: SkillTarget,
        arguments: Mapping[str, Any],
    ) -> tuple[Path, Path, Callable[[], dict[str, Any]]] | ToolResult:
        if action == "create":
            if target.exists:
                return ToolResult(content=f"skill already exists: {target.name}", is_error=True)
            content_result = self._required_text(arguments, "content")
            if isinstance(content_result, ToolResult):
                return content_result
            content = content_result

            def mutate_create() -> dict[str, Any]:
                target.skill_file.parent.mkdir(parents=True, exist_ok=True)
                target.skill_file.write_text(content, encoding="utf-8")
                return self._skill_manage_payload(
                    core=core,
                    action=action,
                    path=target.skill_file,
                    changed=True,
                    message=f"skill created: {target.name}",
                )

            return target.skill_file, target.skill_dir, mutate_create

        if not target.exists:
            return ToolResult(content=f"skill not found: {target.name}", is_error=True)

        if action == "update":
            content_result = self._required_text(arguments, "content")
            if isinstance(content_result, ToolResult):
                return content_result
            content = content_result

            def mutate_update() -> dict[str, Any]:
                previous = target.skill_file.read_text(encoding="utf-8", errors="replace") if target.skill_file.exists() else ""
                target.skill_file.parent.mkdir(parents=True, exist_ok=True)
                target.skill_file.write_text(content, encoding="utf-8")
                return self._skill_manage_payload(
                    core=core,
                    action=action,
                    path=target.skill_file,
                    changed=previous != content,
                    message=f"skill updated: {target.name}",
                )

            return target.skill_file, self._skill_mutation_root(target), mutate_update

        if action == "delete":
            delete_target = self._skill_mutation_root(target)

            def mutate_delete() -> dict[str, Any]:
                self._remove_path(delete_target)
                return self._skill_manage_payload(
                    core=core,
                    action=action,
                    path=delete_target,
                    changed=True,
                    message=f"skill deleted: {target.name}",
                )

            return delete_target, delete_target, mutate_delete

        if action == "patch":
            patch_target_result = self._skill_patch_target(target, arguments.get("file_path"))
            if isinstance(patch_target_result, ToolResult):
                return patch_target_result
            patch_target, display_path = patch_target_result
            old_result = self._required_text(arguments, "old_string")
            if isinstance(old_result, ToolResult):
                return old_result
            if arguments.get("new_string") is None:
                return ToolResult(content="new_string is required", is_error=True)
            old = old_result
            new = str(arguments.get("new_string"))
            replace_all = bool(arguments.get("replace_all", False))

            def mutate_patch() -> dict[str, Any]:
                text = patch_target.read_text(encoding="utf-8", errors="replace")
                match_count = text.count(old)
                if match_count == 0:
                    raise ValueError(f"old_string not found in {display_path}")
                if not replace_all and match_count != 1:
                    raise ValueError(f"old_string matched {match_count} times in {display_path}; set replace_all=true to replace all")
                patched = text.replace(old, new) if replace_all else text.replace(old, new, 1)
                patch_target.write_text(patched, encoding="utf-8")
                diff = "\n".join(
                    difflib.unified_diff(
                        text.splitlines(),
                        patched.splitlines(),
                        fromfile=f"a/{display_path}",
                        tofile=f"b/{display_path}",
                        lineterm="",
                    )
                )
                return self._skill_manage_payload(
                    core=core,
                    action=action,
                    path=patch_target,
                    changed=text != patched,
                    message=diff or f"patched {display_path}",
                    diff=diff,
                )

            return patch_target, self._skill_mutation_root(target), mutate_patch

        if action == "write_file":
            support_target_result = self._skill_support_target(target, arguments.get("file_path"))
            if isinstance(support_target_result, ToolResult):
                return support_target_result
            support_target, display_path = support_target_result
            if arguments.get("file_content") is None:
                return ToolResult(content="file_content is required", is_error=True)
            file_content = str(arguments.get("file_content"))

            def mutate_write_file() -> dict[str, Any]:
                previous = support_target.read_text(encoding="utf-8", errors="replace") if support_target.exists() else None
                support_target.parent.mkdir(parents=True, exist_ok=True)
                support_target.write_text(file_content, encoding="utf-8")
                return self._skill_manage_payload(
                    core=core,
                    action=action,
                    path=support_target,
                    changed=previous != file_content,
                    message=f"skill file written: {display_path}",
                )

            return support_target, target.skill_dir, mutate_write_file

        if action == "remove_file":
            support_target_result = self._skill_support_target(target, arguments.get("file_path"))
            if isinstance(support_target_result, ToolResult):
                return support_target_result
            support_target, display_path = support_target_result
            if not support_target.exists() or support_target.is_symlink() or not support_target.is_file():
                return ToolResult(content=f"skill file not found: {display_path}", is_error=True)

            def mutate_remove_file() -> dict[str, Any]:
                support_target.unlink()
                self._remove_empty_dirs(support_target.parent, stop=target.skill_dir)
                return self._skill_manage_payload(
                    core=core,
                    action=action,
                    path=support_target,
                    changed=True,
                    message=f"skill file removed: {display_path}",
                )

            return support_target, target.skill_dir, mutate_remove_file

        return ToolResult(content=f"unsupported skill_manage action: {action}", is_error=True)

    def _required_text(self, arguments: Mapping[str, Any], key: str) -> str | ToolResult:
        if arguments.get(key) is None or str(arguments.get(key)) == "":
            return ToolResult(content=f"{key} is required", is_error=True)
        return str(arguments.get(key))

    def _skill_mutation_root(self, target: SkillTarget) -> Path:
        return target.skill_dir if target.packaged else target.skill_file

    def _skill_patch_target(self, target: SkillTarget, file_path: Any | None) -> tuple[Path, str] | ToolResult:
        requested_file = str(file_path or "").strip()
        if not requested_file:
            if target.skill_file.is_symlink() or not target.skill_file.is_file():
                return ToolResult(content=f"skill file not readable: {target.name}", is_error=True)
            return target.skill_file, target.skill_file.relative_to(target.skill_dir).as_posix() if target.packaged else target.skill_file.name
        support_target = self._skill_support_target(target, requested_file)
        if isinstance(support_target, ToolResult):
            return support_target
        path, display = support_target
        if path.is_symlink() or not path.is_file():
            return ToolResult(content=f"skill file not readable: {display}", is_error=True)
        return path, display

    def _skill_support_target(self, target: SkillTarget, file_path: Any | None) -> tuple[Path, str] | ToolResult:
        if not target.packaged:
            return ToolResult(content=f"support files require a packaged skill: {target.name}", is_error=True)
        requested = Path(str(file_path or "").strip())
        if (
            not requested.parts
            or requested.is_absolute()
            or ".." in requested.parts
            or any(part.startswith(".") for part in requested.parts)
            or requested.parts[0] not in SKILL_SUPPORT_DIRS
        ):
            allowed = ", ".join(sorted(SKILL_SUPPORT_DIRS))
            return ToolResult(content=f"file_path must be under one of: {allowed}", is_error=True)
        target_path = require_relative_path(target.skill_dir / requested, target.skill_dir)
        if target_path.exists() and target_path.is_symlink():
            return ToolResult(content=f"skill file not writable: {requested.as_posix()}", is_error=True)
        return target_path, requested.as_posix()

    def _apply_skill_mutation(
        self,
        *,
        core: LoadedCore,
        action: str,
        mutation_root: Path,
        mutate: Callable[[], dict[str, Any]],
    ) -> ToolResult:
        with tempfile.TemporaryDirectory(prefix="demiurge-skill-manage-") as tmp:
            backup = Path(tmp) / "backup"
            existed = mutation_root.exists() or mutation_root.is_symlink()
            if existed:
                if mutation_root.is_symlink():
                    return ToolResult(content=f"skill target is a symlink: {mutation_root.relative_to(core.root).as_posix()}", is_error=True)
                if mutation_root.is_dir():
                    shutil.copytree(mutation_root, backup, symlinks=True)
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(mutation_root.read_bytes())
            try:
                payload = mutate()
                CoreLoader().load(core.root)
            except CoreLoadError as exc:
                self._restore_path(mutation_root, backup if existed else None)
                data = {
                    "success": False,
                    "executionStarted": False,
                    "action": action,
                    "path": mutation_root.relative_to(core.root).as_posix(),
                    "error": str(exc),
                }
                return ToolResult(
                    content=f"skill {action} rolled back because core load failed: {exc}",
                    data=data,
                    is_error=True,
                )
            except Exception:
                self._restore_path(mutation_root, backup if existed else None)
                raise
        return ToolResult(content=str(payload.get("message") or f"skill {action} completed"), data=payload)

    def _restore_path(self, path: Path, backup: Path | None) -> None:
        self._remove_path(path)
        if backup is None:
            return
        if backup.is_dir():
            shutil.copytree(backup, path, symlinks=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, path)

    def _remove_path(self, path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    def _remove_empty_dirs(self, path: Path, *, stop: Path) -> None:
        current = path
        while current != stop and current.exists():
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _skill_manage_payload(
        self,
        *,
        core: LoadedCore,
        action: str,
        path: Path,
        changed: bool,
        message: str,
        diff: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": True,
            "executionStarted": True,
            "action": action,
            "path": path.relative_to(core.root).as_posix(),
            "changed": changed,
            "effective_next_turn": True,
            "message": message,
        }
        if diff is not None:
            payload["diff"] = diff
        return payload

    def _skill_view_by_name(self, core: LoadedCore, *, name: str, file_path: Any | None) -> ToolResult:
        if not name:
            return ToolResult(content="name is required", is_error=True)
        skill = core.skill_by_id(name)
        if skill is None:
            available = ", ".join(skill.name for skill in core.skills) or "(none)"
            return ToolResult(content=f"skill not found: {name}. Available skills: {available}", is_error=True)

        requested_file = str(file_path or "").strip()
        if requested_file:
            result = self._skill_linked_file(skill, requested_file)
            if isinstance(result, ToolResult):
                return result
            relative_path, text = result
            payload = {
                "success": True,
                "name": skill.name,
                "skill_id": skill.skill_id,
                "file_path": relative_path,
                "content": text,
                "path": f"{skill.path.parent.relative_to(core.root).as_posix()}/{relative_path}",
            }
            model_output = "\n".join(
                [
                    f"<skill_file name=\"{skill.name}\" file_path=\"{relative_path}\">",
                    text,
                    "</skill_file>",
                ]
            )
            content = json.dumps(payload, ensure_ascii=False)
            return ToolResult(content=content, data=payload, model_output=model_output)

        payload = {
            "success": True,
            "name": skill.name,
            "skill_id": skill.skill_id,
            "description": skill.description,
            "category": skill.category,
            "content": skill.content,
            "path": skill.relative_path,
            "linked_files": skill.linked_files,
        }
        model_output = "\n".join(
            [
                f"<skill name=\"{skill.name}\" id=\"{skill.skill_id}\" path=\"{skill.relative_path}\">",
                skill.content,
                "</skill>",
            ]
        )
        if skill.linked_files:
            linked = [
                path
                for paths in skill.linked_files.values()
                for path in paths
            ]
            model_output += "\n\nLinked files available via skill_view(name, file_path):\n"
            model_output += "\n".join(f"- {path}" for path in linked)
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(content=content, data=payload, model_output=model_output)

    def _skill_linked_file(self, skill: Any, file_path: str) -> tuple[str, str] | ToolResult:
        requested = Path(file_path)
        if requested.is_absolute() or ".." in requested.parts:
            return ToolResult(content="file_path must be a relative linked skill file", is_error=True)
        normalized = requested.as_posix()
        allowed = {
            path
            for paths in skill.linked_files.values()
            for path in paths
        }
        if normalized not in allowed:
            return ToolResult(content=f"linked skill file not found or not allowed: {normalized}", is_error=True)
        target = require_relative_path(skill.path.parent / normalized, skill.path.parent)
        if target.is_symlink() or not target.is_file():
            return ToolResult(content=f"linked skill file not readable: {normalized}", is_error=True)
        text = target.read_text(encoding="utf-8", errors="replace")
        return normalized, text

    def _skill_metadata(self, skill: Any) -> dict[str, Any]:
        return {
            "name": skill.name,
            "skill_id": skill.skill_id,
            "description": skill.description,
            "category": skill.category,
            "path": skill.relative_path,
            "packaged": skill.packaged,
            "linked_files": skill.linked_files,
        }

    def _tools_list(self, core: LoadedCore) -> ToolResult:
        tools = [entry.to_model_metadata() for entry in self.registry_for(core)]
        model_tools = [
            {
                "name": tool["name"],
                "description": tool["description"],
                "source": tool["source"],
                "enabled": tool["enabled"],
            }
            for tool in tools
        ]
        payload = {
            "success": True,
            "count": len(tools),
            "tools": tools,
            "hint": (
                "These are host-visible tools for the current agent core. The `tools` entries include full "
                "host metadata for programmatic inspection; use `model_tools` for user-facing capability summaries."
            ),
            "model_tools": model_tools,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        model_output = json.dumps(
            {
                "success": True,
                "count": len(model_tools),
                "tools": model_tools,
            },
            ensure_ascii=False,
            indent=2,
        )
        return ToolResult(content=content, data=payload, model_output=model_output)

    def _todo(self, call: ToolCall, *, turn: TurnContext) -> ToolResult:
        action = str(call.arguments.get("action") or "list").strip().lower()
        path = self.version_store.home / "runtime" / "session-state" / turn.session_id / "todo.json"
        todos = read_json(path, [])
        if not isinstance(todos, list):
            todos = []
        if action == "add":
            text = str(call.arguments.get("text") or "").strip()
            if not text:
                return ToolResult(content="text is required for todo add", is_error=True)
            todos.append({"text": text, "done": False})
            write_json(path, todos)
            return ToolResult(content=f"todo added: {text}", data={"todos": todos})
        if action == "complete":
            index = int(call.arguments.get("index") or 0)
            if index < 1 or index > len(todos):
                return ToolResult(content=f"todo index out of range: {index}", is_error=True, data={"todos": todos})
            item = todos[index - 1]
            if isinstance(item, dict):
                item["done"] = True
            write_json(path, todos)
            return ToolResult(content=f"todo completed: {index}", data={"todos": todos})
        if action == "clear":
            write_json(path, [])
            return ToolResult(content="todo list cleared", data={"todos": []})
        if action != "list":
            return ToolResult(content=f"unsupported todo action: {action}", is_error=True, data={"todos": todos})
        lines = []
        for index, item in enumerate(todos, start=1):
            if isinstance(item, dict):
                mark = "x" if item.get("done") else " "
                lines.append(f"{index}. [{mark}] {item.get('text', '')}")
        return ToolResult(content="\n".join(lines) or "(no todos)", data={"todos": todos})

    def _clarify(self, call: ToolCall) -> ToolResult:
        question = str(call.arguments.get("question") or "").strip()
        if not question:
            return ToolResult(content="question is required", is_error=True)
        raw_choices = call.arguments.get("choices") or []
        choices = [str(choice).strip() for choice in raw_choices if str(choice).strip()] if isinstance(raw_choices, list) else []
        return ToolResult(
            content=question,
            data={"needs_user": True, "question": question, "choices": choices[:4]},
            terminate=True,
            model_output=f"Need user input: {question}",
        )

    async def _web_extract(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        capability.require("network.fetch")
        url = str(call.arguments.get("url") or "").strip()
        if not url:
            return ToolResult(content="url is required", is_error=True)
        if not url.startswith(("http://", "https://")):
            return ToolResult(content="only http and https URLs are supported", is_error=True)
        denied = await self._approval_for_url(call, core=core, turn=turn, url=url, emit_event=emit_event)
        if denied:
            return denied
        timeout = self._positive_int(call.arguments.get("timeout_seconds"), default=20, maximum=60)
        max_chars = self._positive_int(
            call.arguments.get("max_chars"),
            default=DEFAULT_TOOL_OUTPUT_LIMIT_CHARS,
            maximum=DEFAULT_TOOL_OUTPUT_LIMIT_CHARS,
        )

        def fetch() -> tuple[int, str | None, str]:
            request = urllib.request.Request(url, method="GET", headers={"User-Agent": "demiurge/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read(max_chars + 1).decode(charset, errors="replace")
                return response.status, response.headers.get("content-type"), body

        try:
            status, content_type, body = await asyncio.to_thread(fetch)
        except urllib.error.URLError as exc:
            return ToolResult(content=f"web_extract failed: {exc}", is_error=True, data={"executionStarted": True})
        truncated = len(body) > max_chars
        if truncated:
            body = body[:max_chars]
        content = truncate_text(body, limit=max_chars)
        return ToolResult(
            content=content,
            data={
                "executionStarted": True,
                "url": url,
                "status": status,
                "content_type": content_type,
                "truncated": truncated,
            },
            model_output=content,
        )

    def _session_search(self, call: ToolCall) -> ToolResult:
        store = self.session_runtime
        if store is None:
            return ToolResult(content="session runtime is not configured", is_error=True)
        query = str(call.arguments.get("query") or "").strip()
        session_id = str(call.arguments.get("session_id") or "").strip()
        limit = self._positive_int(call.arguments.get("limit"), default=10, maximum=50)
        if session_id:
            try:
                messages = store.read_messages(session_id)
            except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
                return ToolResult(content=f"session_search failed: {exc}", is_error=True)
            selected = messages[-limit:] if not query else [
                message
                for message in messages
                if query.lower() in message.content.lower()
            ][:limit]
            results = [
                {
                    "session_id": message.session_id,
                    "message_id": message.id,
                    "turn_id": message.turn_id,
                    "role": message.role,
                    "created_at": message.created_at,
                    "content": truncate_text(message.content, limit=800),
                }
                for message in selected
            ]
            return self._session_search_result(results)
        if not query:
            sessions = [
                {
                    "session_id": record.session_id,
                    "updated_at": record.updated_at,
                    "title": record.title,
                    "preview": record.preview,
                    "message_count": record.message_count,
                }
                for record in store.list_sessions(limit=limit)
            ]
            content = json.dumps({"sessions": sessions}, ensure_ascii=False)
            return ToolResult(content=content, data={"sessions": sessions}, model_output=content)
        results: list[dict[str, Any]] = []
        for record in store.list_sessions(limit=10_000):
            try:
                messages = store.read_messages(record.session_id)
            except (OSError, json.JSONDecodeError):
                continue
            for message in messages:
                if query.lower() not in message.content.lower():
                    continue
                results.append(
                    {
                        "session_id": message.session_id,
                        "message_id": message.id,
                        "turn_id": message.turn_id,
                        "role": message.role,
                        "created_at": message.created_at,
                        "content": truncate_text(message.content, limit=800),
                    }
                )
                if len(results) >= limit:
                    return self._session_search_result(results)
        return self._session_search_result(results)

    def _session_search_result(self, results: list[dict[str, Any]]) -> ToolResult:
        content = json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
        return ToolResult(
            content=content,
            data={"results": results, "count": len(results)},
            model_output=content,
        )

    async def _schedule_manage(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: EventEmitter | None,
    ) -> ToolResult:
        action = str(call.arguments.get("action") or "list").strip().lower()
        if action not in {"list", "create", "update", "enable", "disable", "delete"}:
            return ToolResult(content=f"unsupported schedule_manage action: {action}", is_error=True)
        manager = ScheduleManager(core)
        try:
            if action == "list":
                return self._schedule_manage_result(manager.list())

            capability.require("schedule.manage")
            schedule_id = str(call.arguments.get("schedule_id") or "").strip() or None
            schedule = call.arguments.get("schedule")
            prompt = call.arguments.get("prompt")
            if action == "create":
                if schedule is None or not str(schedule).strip():
                    return ToolResult(content="schedule is required for create", is_error=True)
                if prompt is None or not str(prompt).strip():
                    return ToolResult(content="prompt is required for create", is_error=True)
            elif action in {"update", "enable", "disable", "delete"} and not schedule_id:
                return ToolResult(content=f"schedule_id is required for {action}", is_error=True)
            if action == "update" and schedule is None and prompt is None:
                return ToolResult(content="update requires schedule or prompt", is_error=True)

            target = f"schedules/{schedule_id or '(auto)'}"
            denied = await self._approval_for_schedule_manage(
                call,
                core=core,
                turn=turn,
                action=action,
                target=target,
                emit_event=emit_event,
            )
            if denied:
                return denied

            if action == "create":
                payload = manager.create(
                    schedule_id=schedule_id,
                    schedule=str(schedule),
                    prompt=str(prompt),
                )
            elif action == "update":
                payload = manager.update(
                    schedule_id=str(schedule_id),
                    schedule=None if schedule is None else str(schedule),
                    prompt=None if prompt is None else str(prompt),
                )
            elif action == "enable":
                payload = manager.set_enabled(schedule_id=str(schedule_id), enabled=True)
            elif action == "disable":
                payload = manager.set_enabled(schedule_id=str(schedule_id), enabled=False)
            else:
                payload = manager.delete(schedule_id=str(schedule_id))
            return self._schedule_manage_result(payload)
        except ScheduleManagementError as exc:
            return ToolResult(content=str(exc), is_error=True, data={"executionStarted": False})

    def _schedule_manage_result(self, payload: dict[str, Any]) -> ToolResult:
        payload = {
            **payload,
            "runtime_timezone": self.runtime_timezone.name,
            "runtime_timezone_source": self.runtime_timezone.source,
            "runtime_local_now": self.runtime_timezone.local_now().isoformat(),
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return ToolResult(content=content, data=payload, model_output=content)

    async def _approval_for_path(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability_name: str,
        action: str,
        target: str,
        risk: str,
        summary: str,
        auto_approve: bool,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        policy = self._effective_approval_policy(
            core,
            tool_name=call.name,
            capability_name=capability_name,
            risk=risk,
            default_auto=auto_approve,
        )
        request = ApprovalRequest(
            tool_name=call.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability=capability_name,
            action=action,
            risk=risk,
            summary=summary,
            target=target,
            arguments_preview={"path": target},
            cache_key=f"{call.name}:{capability_name}:{action}:{target}",
            auto_approve=policy == "auto",
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(request, emit_event=self._turn_event_emitter(emit_event, turn))
        if decision.allowed:
            return None
        return ToolResult(
            content=f"approval denied: {summary}",
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
        )

    async def _approval_for_command(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        cwd: str,
        command: str,
        env_keys: list[str],
        env_overlay: Mapping[str, str],
        execution_options: Mapping[str, Any],
        command_guard: CommandGuardDecision,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        requires_explicit_approval = command_guard.action != "allow"
        if command_guard.action == "allow":
            policy = self._safe_command_approval_policy(
                core,
                tool_name=call.name,
                capability_name="terminal.exec",
                risk=command_guard.risk,
            )
            auto_approve = policy != "deny"
        else:
            policy = self._effective_approval_policy(
                core,
                tool_name=call.name,
                capability_name="terminal.exec",
                risk=command_guard.risk,
                default_auto=False,
            )
            if requires_explicit_approval and policy == "auto":
                policy = "prompt"
            auto_approve = policy == "auto" and not requires_explicit_approval
        summary = f"Run terminal command in {cwd}"
        if command_guard.action != "allow":
            summary = f"{summary}: {command_guard.reason}"
        request = ApprovalRequest(
            tool_name=call.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability="terminal.exec",
            action="exec",
            risk=command_guard.risk,
            summary=summary,
            target=cwd,
            command=command,
            arguments_preview={
                "cwd": cwd,
                "command": command,
                "env_keys": env_keys,
                "command_guard": asdict(command_guard),
            },
            cache_key=_terminal_approval_cache_key(
                command,
                command_guard,
                cwd=cwd,
                env_overlay=env_overlay,
                execution_options=execution_options,
            ),
            auto_approve=auto_approve,
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(request, emit_event=self._turn_event_emitter(emit_event, turn))
        if decision.allowed:
            return None
        content = f"approval denied: terminal command in {cwd}"
        return ToolResult(
            content=content,
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
            display_output=self._format_command_display(command, cwd, content),
        )

    async def _approval_for_url(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        url: str,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        policy = self._effective_approval_policy(
            core,
            tool_name=call.name,
            capability_name="network.fetch",
            risk="high",
            default_auto=False,
        )
        request = ApprovalRequest(
            tool_name=call.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability="network.fetch",
            action="fetch",
            risk="high",
            summary=f"Extract URL {url}",
            target=url,
            arguments_preview={"url": url},
            cache_key=f"web_extract:network.fetch:{url}",
            auto_approve=policy == "auto",
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(request, emit_event=self._turn_event_emitter(emit_event, turn))
        if decision.allowed:
            return None
        return ToolResult(
            content=f"approval denied: extract URL {url}",
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
        )

    async def _approval_for_mcp(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        tool: McpToolInfo,
        capability_name: str,
        risk: str,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        policy = self._effective_approval_policy(
            core,
            tool_name=call.name,
            capability_name=capability_name,
            risk=risk,
            default_auto=tool.approval_policy == "auto",
        )
        target = f"{tool.server_id}/{tool.server_tool_name}"
        request = ApprovalRequest(
            tool_name=call.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability=capability_name,
            action="mcp.call",
            risk=risk,
            summary=f"Call MCP tool {target}",
            target=target,
            arguments_preview=dict(call.arguments),
            cache_key=f"{call.name}:{capability_name}:mcp.call:{target}",
            auto_approve=policy == "auto",
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(request, emit_event=self._turn_event_emitter(emit_event, turn))
        if decision.allowed:
            return None
        return ToolResult(
            content=f"approval denied: MCP tool {target}",
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
        )

    async def _approval_for_skill_manage(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        action: str,
        target: str,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        policy = self._effective_approval_policy(
            core,
            tool_name=call.name,
            capability_name="fs.write",
            risk="high",
            default_auto=False,
        )
        request = ApprovalRequest(
            tool_name=call.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability="fs.write",
            action=f"skill.{action}",
            risk="high",
            summary=f"{action} skill {target}",
            target=target,
            arguments_preview={"skill_path": target, "action": action},
            cache_key=f"skill_manage:fs.write:{action}:{target}",
            auto_approve=policy == "auto",
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(request, emit_event=self._turn_event_emitter(emit_event, turn))
        if decision.allowed:
            return None
        return ToolResult(
            content=f"approval denied: {action} skill {target}",
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
        )

    async def _approval_for_schedule_manage(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        action: str,
        target: str,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        policy = self._effective_approval_policy(
            core,
            tool_name=call.name,
            capability_name="schedule.manage",
            risk="high",
            default_auto=False,
        )
        prompt = str(call.arguments.get("prompt") or "")
        request = ApprovalRequest(
            tool_name=call.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability="schedule.manage",
            action=f"schedule.{action}",
            risk="high",
            summary=f"{action} schedule {target}",
            target=target,
            arguments_preview={
                "action": action,
                "schedule_id": call.arguments.get("schedule_id"),
                "schedule": call.arguments.get("schedule"),
                "prompt_preview": truncate_text(prompt, limit=200) if prompt else None,
            },
            cache_key=f"schedule_manage:schedule.manage:{action}:{target}",
            auto_approve=policy == "auto",
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(request, emit_event=self._turn_event_emitter(emit_event, turn))
        if decision.allowed:
            return None
        return ToolResult(
            content=f"approval denied: {action} schedule {target}",
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
        )

    def _turn_event_emitter(self, emit_event: EventEmitter | None, turn: TurnContext) -> EventEmitter | None:
        if emit_event is None:
            return None

        def wrapped(event_type: str, **data: Any) -> dict[str, Any]:
            payload = {**turn.metadata, **data}
            return emit_event(event_type, **payload)

        return wrapped

    async def _approval_for_authored(
        self,
        entry: ToolRegistryEntry,
        slot: SlotDefinition,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        emit_event: EventEmitter | None,
    ) -> ToolResult | None:
        capability_name = entry.capability or f"tool.call:{entry.name}"
        policy = entry.approval_policy
        for configured in (
            self._select_approval_policy(
                core.manifest.approval,
                tool_name=entry.name,
                capability_name=capability_name,
                risk=entry.risk,
            ),
            self._select_approval_policy(
                self.global_approval,
                tool_name=entry.name,
                capability_name=capability_name,
                risk=entry.risk,
            ),
        ):
            if configured:
                policy = self._stricter_policy(policy, configured)
        summary = f"Call authored tool {entry.name}"
        request = ApprovalRequest(
            tool_name=entry.name,
            tool_call_id=call.id,
            turn_id=turn.turn_id,
            capability=capability_name,
            action="authored.call",
            risk=entry.risk,
            summary=summary,
            target=slot.relative_path,
            arguments_preview=_safe_authored_arguments_preview(call.arguments),
            cache_key=(
                f"{entry.name}:{capability_name}:authored.call:{slot.relative_path}"
            ),
            auto_approve=policy == "auto",
            policy=policy,
            session_id=turn.session_id,
        )
        decision = await self.approval_runtime.decide(
            request,
            emit_event=self._turn_event_emitter(emit_event, turn),
        )
        if decision.allowed:
            return None
        return ToolResult(
            content=f"approval denied: {summary}",
            data={"executionStarted": False, "approval": asdict(decision)},
            is_error=True,
        )

    async def _execute_authored(
        self,
        slot: SlotDefinition,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        output_factory: Callable[[SlotDefinition], Any] | None = None,
    ) -> ToolResult:
        func = load_slot_callable(slot)
        output = output_factory(slot) if output_factory is not None else None
        ctx = ToolContext(
            turn=turn,
            slot_id=slot.slot_id,
            slot_path=slot.relative_path,
            capability=capability,
            output=output,
            workspace=self.workspace.root,
        )
        try:
            value = func(ctx, call.arguments)
            if inspect.isawaitable(value):
                value = await value
            flush_slot_end = getattr(output, "flush_slot_end", None)
            if callable(flush_slot_end):
                flush_slot_end()
        except Exception as exc:
            return ToolResult(
                content=str(exc),
                data={"executionStarted": True},
                is_error=True,
            )
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, dict):
            return ToolResult(
                content=str(value.get("content", "")),
                data=value.get("data"),
                is_error=bool(value.get("is_error", False)),
                terminate=bool(value.get("terminate", False)),
                model_output=value.get("model_output"),
                display_output=value.get("display_output"),
            )
        return ToolResult(content=str(value))

    def _effective_approval_policy(
        self,
        core: LoadedCore,
        *,
        tool_name: str,
        capability_name: str,
        risk: str,
        default_auto: bool,
    ) -> str:
        baseline = "auto" if default_auto else "prompt"
        entry = next((item for item in self.registry_for(core) if item.name == tool_name), None)
        if entry:
            baseline = self._stricter_policy(baseline, entry.approval_policy)
        core_policy = self._select_approval_policy(
            core.manifest.approval,
            tool_name=tool_name,
            capability_name=capability_name,
            risk=risk,
        )
        if core_policy:
            baseline = self._stricter_policy(baseline, core_policy)
        global_policy = self._select_approval_policy(
            self.global_approval,
            tool_name=tool_name,
            capability_name=capability_name,
            risk=risk,
        )
        if global_policy:
            return global_policy
        return baseline

    def _safe_command_approval_policy(
        self,
        core: LoadedCore,
        *,
        tool_name: str,
        capability_name: str,
        risk: str,
    ) -> str:
        global_policy = self._select_approval_policy(
            self.global_approval,
            tool_name=tool_name,
            capability_name=capability_name,
            risk=risk,
        )
        if global_policy == "deny":
            return "deny"
        if global_policy == "auto":
            return "auto"
        core_policy = self._select_approval_policy(
            core.manifest.approval,
            tool_name=tool_name,
            capability_name=capability_name,
            risk=risk,
        )
        if core_policy == "deny":
            return "deny"
        return "auto"

    def _select_approval_policy(
        self,
        config: ApprovalInfo | None,
        *,
        tool_name: str,
        capability_name: str,
        risk: str,
    ) -> str | None:
        if config is None:
            return None
        value = (
            config.tools.get(tool_name)
            or config.capabilities.get(capability_name)
            or config.risks.get(risk)
            or config.default
        )
        if not value:
            return None
        return self._normalize_approval_policy(value)

    def _stricter_policy(self, left: str, right: str) -> str:
        left = self._normalize_approval_policy(left)
        right = self._normalize_approval_policy(right)
        return left if APPROVAL_ORDER[left] >= APPROVAL_ORDER[right] else right

    def _normalize_approval_policy(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in APPROVAL_ORDER:
            raise ValueError(f"invalid approval policy: {value}")
        return normalized

    def _normalize_risk(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in RISK_ORDER:
            raise ValueError(f"invalid tool risk: {value}")
        return normalized

    def _positive_int(self, value: Any, *, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if parsed <= 0:
            parsed = default
        return min(parsed, maximum)

    def _format_command_result(self, completed: subprocess.CompletedProcess[str]) -> str:
        return self._format_command_output(completed.returncode, completed.stdout, completed.stderr, timed_out=False)

    def _format_command_display(self, command: str, cwd: str, content: str) -> str:
        parts = [f"$ {command}", f"cwd: {cwd}"]
        if content:
            parts.append(content)
        return "\n".join(parts)

    def _format_command_output(self, exit_code: int, stdout: str, stderr: str, *, timed_out: bool) -> str:
        parts = [f"exit_code: {exit_code}"]
        if timed_out:
            parts.append("timed_out: true")
        if stdout:
            parts.append("stdout:\n" + truncate_text(stdout, limit=DEFAULT_TOOL_OUTPUT_LIMIT_CHARS))
        if stderr:
            parts.append("stderr:\n" + truncate_text(stderr, limit=DEFAULT_TOOL_OUTPUT_LIMIT_CHARS))
        return "\n".join(parts)
