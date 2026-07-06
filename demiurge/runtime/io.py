from __future__ import annotations

from dataclasses import asdict
from typing import Any, Protocol

from demiurge.providers import ToolCall
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, ToolInteractionRecord
from demiurge.runtime.session import SessionRuntime
from demiurge.sdk import ToolResult, TurnContext
from demiurge.storage import SessionMessage
from demiurge.tools.records import ToolExecutionRecord


class TurnIOHost(Protocol):
    @property
    def session_id(self) -> str:
        ...

    @property
    def session_runtime(self) -> SessionRuntime:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def truncate_model_content(self, content: str) -> str:
        ...

    def tool_result_model_content(self, result: ToolResult) -> str:
        ...

    def schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        ...

    async def dispatch_interaction_item_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        ...


class RunnerTurnIOHost:
    """Adapter from SessionTurnStepRunner to TurnIOHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def session_id(self) -> str:
        return self.runner.session_id

    @property
    def session_runtime(self) -> SessionRuntime:
        return self.runner.session_runtime

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.event_log.emit(event_type, **payload)

    def truncate_model_content(self, content: str) -> str:
        return self.runner._truncate_model_content(content)

    def tool_result_model_content(self, result: ToolResult) -> str:
        return self.runner._tool_result_model_content(result)

    def schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.runner.interaction_dispatch.schedule(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )

    async def dispatch_interaction_item_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.runner.interaction_dispatch.dispatch_now(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )


class TurnIO:
    """Host-mediated IO for one turn's transcript and channel items."""

    def __init__(self, host: TurnIOHost):
        self.host = host

    def send_user(
        self,
        *,
        turn_id: str,
        content: str,
        interaction_metadata: dict[str, Any],
    ) -> SessionMessage | None:
        if not content:
            return None
        message = self.host.session_runtime.append_message(
            self.host.session_id,
            role="user",
            content=content,
            turn_id=turn_id,
            interaction_metadata=interaction_metadata,
        )
        self.host.emit_event(
            "message.persisted",
            turn_id=turn_id,
            message_id=message.id,
            role=message.role,
            kind=message.kind,
            **interaction_metadata,
        )
        return message

    async def send_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ) -> tuple[SessionMessage, list[InteractionItem]]:
        message = self.host.session_runtime.append_message(
            self.host.session_id,
            role="assistant",
            content=content,
            turn_id=turn.turn_id,
            visible=bool(content.strip()),
            model_visible=True,
            interaction_metadata=interaction_metadata,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "interim": bool(content.strip()),
                "tool_calls": [asdict(call) for call in tool_calls],
            },
        )
        self.host.emit_event(
            "message.persisted",
            turn_id=turn.turn_id,
            step_id=step_id,
            message_id=message.id,
            role=message.role,
            kind=message.kind,
            tool_calls=[asdict(call) for call in tool_calls],
            **interaction_metadata,
        )
        item = self._assistant_interim_item(
            content,
            turn=turn,
            step_id=step_id,
            message_id=message.id,
            interaction_metadata=interaction_metadata,
        )
        if item is None:
            return message, []
        self.host.schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return message, [item]

    def send_tool_result(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        message = self._persist_tool_result_message(
            turn=turn,
            step_id=step_id,
            record=record,
            interaction_metadata=interaction_metadata,
        )
        item = InteractionItem.tool_result_item(
            record,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "message_id": message.id,
                "tool_name": record.call.name,
                "tool_call_id": record.call.id,
                "is_error": record.result.is_error,
            },
        )
        self.host.schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return item

    async def send_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        record = ToolInteractionRecord.started(
            call,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "tool_name": call.name,
                "tool_call_id": call.id,
                "tool_phase": "start",
            },
        )
        item = InteractionItem.tool_call_item(record)
        await self.host.dispatch_interaction_item_now(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return item

    async def send_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        message = self._persist_tool_result_message(
            turn=turn,
            step_id=step_id,
            record=record,
            interaction_metadata=interaction_metadata,
        )
        tool_record = ToolInteractionRecord.finished(
            record,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "message_id": message.id,
                "tool_name": record.call.name,
                "tool_call_id": record.call.id,
                "tool_phase": "finish",
                "is_error": record.result.is_error,
            },
        )
        item = InteractionItem.tool_call_item(tool_record)
        await self.host.dispatch_interaction_item_now(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return item

    def _persist_tool_result_message(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> SessionMessage:
        content = self.host.truncate_model_content(self.host.tool_result_model_content(record.result))
        message = self.host.session_runtime.append_message(
            self.host.session_id,
            role="tool",
            content=content,
            turn_id=turn.turn_id,
            visible=False,
            model_visible=True,
            interaction_metadata=interaction_metadata,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "tool_name": record.call.name,
                "tool_call_id": record.call.id,
                "is_error": record.result.is_error,
            },
        )
        self.host.emit_event(
            "message.persisted",
            turn_id=turn.turn_id,
            step_id=step_id,
            message_id=message.id,
            role=message.role,
            kind=message.kind,
            tool_name=record.call.name,
            **interaction_metadata,
        )
        return message

    def _assistant_interim_item(
        self,
        content: str,
        *,
        turn: TurnContext,
        step_id: str,
        message_id: str,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        text = content.strip()
        if not text:
            return None
        metadata = {
            "phase": "model_step",
            "step_id": step_id,
            "message_id": message_id,
            "interim": True,
            "history_policy": "persist",
            "delivery": "immediate",
            "delivery_status": "pending",
        }
        delivery = InteractionDelivery(
            type="text",
            kind="message",
            text=text,
            fallback_text=text,
            blocks=[{"type": "text", "text": text, "metadata": {"interim": True, "step_id": step_id}}],
            payload={"type": "text", "text": text},
            visible=True,
            history_policy="persist",
            metadata=metadata,
        )
        self.host.emit_event(
            "message.interim",
            turn_id=turn.turn_id,
            step_id=step_id,
            message_id=message_id,
            content=text,
            **interaction_metadata,
        )
        return InteractionItem.delivery_item(delivery)
