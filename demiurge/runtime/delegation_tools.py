from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from demiurge.core import LoadedCore
from demiurge.providers import ToolCall
from demiurge.runtime.child_agents import ChildAgentRuntime
from demiurge.runtime.scope import PrincipalScope
from demiurge.runtime.tasks import RuntimeTaskKindError, RuntimeTaskRecord, RuntimeTaskWorker
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
        principal_scope: PrincipalScope,
    ) -> ToolResult:
        try:
            if call.name == "delegate_task":
                return await self.host.child_agents.handle_delegate_task(
                    call,
                    core=core,
                    turn=turn,
                    capability=capability,
                )
            if call.name == "task_status":
                return self._task_status(call, principal_scope=principal_scope)
            if call.name == "task_control":
                return await self._task_control(
                    call,
                    principal_scope=principal_scope,
                )
            if call.name == "yield_until":
                return await self._yield_until(
                    call,
                    principal_scope=principal_scope,
                )
            return ToolResult(content=f"unsupported delegation tool: {call.name}", is_error=True)
        except CapabilityDenied as exc:
            return ToolResult(
                content=str(exc),
                is_error=True,
                data={
                    "executionStarted": False,
                    "denial": "capability",
                },
            )

    def _task_status(
        self,
        call: ToolCall,
        *,
        principal_scope: PrincipalScope,
    ) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        payload = self._task_view(
            task_id,
            principal_scope=principal_scope,
        )
        if payload is None:
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(content=content, data=payload, model_output=content)

    async def _task_control(
        self,
        call: ToolCall,
        *,
        principal_scope: PrincipalScope,
    ) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        command = str(call.arguments.get("command") or "cancel").strip()
        if command != "cancel":
            return ToolResult(content=f"unsupported task_control command: {command}", is_error=True)
        try:
            record = await self.host.task_worker.cancel_owned(
                principal_scope,
                task_id,
            )
            payload = self._model_task_payload(record)
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    async def _yield_until(
        self,
        call: ToolCall,
        *,
        principal_scope: PrincipalScope,
    ) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        raw_timeout = call.arguments.get("timeout_seconds")
        timeout = float(raw_timeout if raw_timeout is not None else 30)
        try:
            record = await self.host.task_worker.wait_owned(
                principal_scope,
                task_id,
                timeout_seconds=timeout,
                consume_completion=True,
            )
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        except asyncio.TimeoutError:
            payload = self._task_view(
                task_id,
                principal_scope=principal_scope,
            ) or {"task_id": task_id, "status": "unknown"}
            payload["timed_out"] = True
            return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)
        payload = self._model_task_payload(record)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def _task_view(
        self,
        task_id: str,
        *,
        principal_scope: PrincipalScope,
    ) -> dict[str, Any] | None:
        try:
            record = self.host.task_worker.get_owned(principal_scope, task_id)
        except (KeyError, RuntimeTaskKindError):
            return None
        return self._model_task_payload(record)

    @staticmethod
    def _model_task_payload(record: RuntimeTaskRecord) -> dict[str, Any]:
        return record.to_model_payload()
