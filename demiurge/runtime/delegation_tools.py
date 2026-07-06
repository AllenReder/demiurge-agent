from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from demiurge.core import LoadedCore
from demiurge.providers import ToolCall
from demiurge.runtime.child_agents import ChildAgentRuntime
from demiurge.runtime.tasks import RuntimeTaskKindError, RuntimeTaskWorker
from demiurge.sdk import ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade


DELEGATION_TOOL_NAMES = {"delegate_task", "task_status", "task_control", "yield_until"}


class DelegationToolHost(Protocol):
    @property
    def child_agents(self) -> ChildAgentRuntime:
        ...

    @property
    def task_worker(self) -> RuntimeTaskWorker:
        ...

    @property
    def tool_runtime(self) -> Any:
        ...


class RunnerDelegationToolHost:
    """Adapter from SessionTurnStepRunner to DelegationToolHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def child_agents(self) -> ChildAgentRuntime:
        return self.runner.child_agents

    @property
    def task_worker(self) -> RuntimeTaskWorker:
        return self.runner.task_worker

    @property
    def tool_runtime(self) -> Any:
        return self.runner.tool_runtime


class DelegationToolRuntime:
    """Handles model-facing delegation and background-task control tools."""

    def __init__(self, host: DelegationToolHost):
        self.host = host

    def can_handle(self, name: str) -> bool:
        return name in DELEGATION_TOOL_NAMES

    async def execute(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
    ) -> ToolResult:
        try:
            visible_tools = {entry.name for entry in self.host.tool_runtime.registry_for(core, turn=turn)}
            if call.name not in visible_tools:
                return ToolResult(content=f"builtin tool is not allowed: {call.name}", is_error=True)
            if call.name == "delegate_task":
                return await self.host.child_agents.handle_delegate_task(
                    call,
                    core=core,
                    turn=turn,
                    capability=capability,
                )
            if call.name == "task_status":
                capability.require("task.control")
                return self._task_status(call)
            if call.name == "task_control":
                capability.require("task.control")
                return await self._task_control(call)
            if call.name == "yield_until":
                capability.require("task.control")
                return await self._yield_until(call)
            return ToolResult(content=f"unsupported delegation tool: {call.name}", is_error=True)
        except CapabilityDenied as exc:
            return ToolResult(content=str(exc), is_error=True, data={"executionStarted": False})

    def _task_status(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        view = str(call.arguments.get("view") or "model").strip()
        payload = self._task_view(task_id, include_log=view in {"operator", "debug"})
        if payload is None:
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(content=content, data=payload, model_output=content)

    async def _task_control(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        command = str(call.arguments.get("command") or "cancel").strip()
        if command != "cancel":
            return ToolResult(content=f"unsupported task_control command: {command}", is_error=True)
        try:
            record = await self.host.task_worker.cancel(task_id)
            payload = record.to_payload(include_log=True, log=self.host.task_worker.log(task_id))
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    async def _yield_until(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        raw_timeout = call.arguments.get("timeout_seconds")
        timeout = float(raw_timeout if raw_timeout is not None else 30)
        try:
            record = await self.host.task_worker.wait(task_id, timeout_seconds=timeout, consume_completion=True)
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        except asyncio.TimeoutError:
            payload = self._task_view(task_id, include_log=False) or {"task_id": task_id, "status": "unknown"}
            payload["timed_out"] = True
            return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)
        payload = record.to_payload(include_log=True, log=self.host.task_worker.log(task_id))
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def _task_view(self, task_id: str, *, include_log: bool) -> dict[str, Any] | None:
        try:
            record = self.host.task_worker.get(task_id)
        except (KeyError, RuntimeTaskKindError):
            return None
        return record.to_payload(include_log=include_log, log=self.host.task_worker.log(task_id) if include_log else None)
