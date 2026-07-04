from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from demiurge.core import LoadedCore
from demiurge.providers import LLMMessage, LLMRequest, ToolDefinition
from demiurge.runtime.interactions import InteractionBridge, InteractionItem, get_current_bridge
from demiurge.runtime.store import RuntimeEvent
from demiurge.sdk import ContextContribution, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityFacade
from demiurge.tools.records import ToolExecutionRecord


@dataclass(slots=True)
class TurnEngineRequest:
    core: LoadedCore
    turn: TurnContext
    capability: CapabilityFacade
    context: list[ContextContribution]
    available_tools: list[ToolDefinition]
    interaction_metadata: dict[str, Any]
    interaction_bridge: InteractionBridge | None = None
    use_bootstrap_context: bool = True


@dataclass(slots=True)
class TurnEngineResult:
    final_output: str
    needs_user: bool = False
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)
    turn_messages: list[LLMMessage] = field(default_factory=list)
    items: list[InteractionItem] = field(default_factory=list)


class TurnEngine:
    """Runs the provider/tool loop for one agent.turn task."""

    def __init__(self, host: Any):
        self.host = host

    async def run(self, request: TurnEngineRequest) -> TurnEngineResult:
        turn_messages: list[LLMMessage] = [LLMMessage(role="user", content=request.turn.user_input.content)]
        tool_records: list[ToolExecutionRecord] = []
        items: list[InteractionItem] = []
        final_output = ""
        needs_user = False
        max_model_steps = request.core.manifest.runtime.max_model_steps
        for step_index in range(1, max_model_steps + 1):
            step_id = f"{request.turn.turn_id}_step_{step_index}"
            self.host.event_log.emit(
                "step.started",
                turn_id=request.turn.turn_id,
                step_id=step_id,
                tools=[tool.name for tool in request.available_tools],
                **request.interaction_metadata,
            )
            messages = self.host._build_messages(
                request.core,
                request.context,
                turn_messages,
                turn_id=request.turn.turn_id,
                step_id=step_id,
                use_bootstrap_context=request.use_bootstrap_context,
            )
            await self.host._maybe_deliver_system_prompt_debug(
                messages,
                turn=request.turn,
                step_id=step_id,
                interaction_metadata=request.interaction_metadata,
                interaction_bridge=request.interaction_bridge or get_current_bridge(),
            )
            provider_request = LLMRequest(
                model=self.host._resolve_model_name(request.core),
                messages=messages,
                tools=request.available_tools,
                metadata={"turn_id": request.turn.turn_id, "step_id": step_id},
            )
            response = await self.host.provider.complete(provider_request)
            if response.tool_calls:
                turn_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                        persist=False,
                    )
                )
                _, interim_items = await self.host.runtime_io.send_assistant_step(
                    turn=request.turn,
                    step_id=step_id,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    interaction_metadata=request.interaction_metadata,
                    interaction_bridge=request.interaction_bridge or get_current_bridge(),
                )
                items.extend(interim_items)
                self.host.event_log.emit(
                    "actions.requested",
                    turn_id=request.turn.turn_id,
                    step_id=step_id,
                    actions=[asdict(call) for call in response.tool_calls],
                    **request.interaction_metadata,
                )
                for call in response.tool_calls:
                    items.append(
                        await self.host.runtime_io.send_tool_call_started(
                            turn=request.turn,
                            step_id=step_id,
                            call=call,
                            interaction_metadata=request.interaction_metadata,
                            interaction_bridge=request.interaction_bridge or get_current_bridge(),
                        )
                    )
                    self._append_runtime_event(
                        RuntimeEvent(
                            type="tool.call.started",
                            aggregate_type="tool_call",
                            aggregate_id=call.id,
                            payload={
                                "turn_id": request.turn.turn_id,
                                "tool_name": call.name,
                                "status": "running",
                                "args": dict(call.arguments),
                            },
                        )
                    )
                terminated = False
                for call in response.tool_calls:
                    tool_items: list[InteractionItem] = []
                    result: ToolResult = await self.host.execute_tool(
                        call,
                        core=request.core,
                        turn=request.turn,
                        capability=request.capability,
                        emit_event=self.host.event_log.emit,
                        output_factory=lambda slot: self.host._module_io_client(
                            slot,
                            turn=request.turn,
                            capability=request.capability,
                            interaction_metadata=request.interaction_metadata,
                            interaction_bridge=request.interaction_bridge or get_current_bridge(),
                            items=tool_items,
                        ),
                    )
                    self._append_runtime_event(
                        RuntimeEvent(
                            type="tool.call.failed" if result.is_error else "tool.call.completed",
                            aggregate_type="tool_call",
                            aggregate_id=call.id,
                            payload={
                                "turn_id": request.turn.turn_id,
                                "tool_name": call.name,
                                "status": "failed" if result.is_error else "succeeded",
                                "result": {
                                    "content": result.content,
                                    "data": result.data,
                                    "is_error": result.is_error,
                                    "terminate": result.terminate,
                                },
                            },
                        )
                    )
                    items.extend(tool_items)
                    record = ToolExecutionRecord(call=call, result=result)
                    tool_records.append(record)
                    turn_messages.append(
                        LLMMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.id,
                            content=self.host._truncate_model_content(self.host._tool_result_model_content(result)),
                            persist=False,
                        )
                    )
                    self.host.event_log.emit(
                        "action.result",
                        turn_id=request.turn.turn_id,
                        step_id=step_id,
                        tool_name=call.name,
                        tool_call_id=call.id,
                        content=result.content,
                        model_output=result.model_output,
                        display_output=result.display_output,
                        data=result.data,
                        is_error=result.is_error,
                        terminate=result.terminate,
                        **request.interaction_metadata,
                    )
                    items.append(
                        await self.host.runtime_io.send_tool_call_finished(
                            turn=request.turn,
                            step_id=step_id,
                            record=record,
                            interaction_metadata=request.interaction_metadata,
                            interaction_bridge=request.interaction_bridge or get_current_bridge(),
                        )
                    )
                    if result.terminate:
                        final_output = result.content
                        needs_user = bool(isinstance(result.data, dict) and result.data.get("needs_user"))
                        turn_messages.append(LLMMessage(role="assistant", content=final_output))
                        self.host.event_log.emit(
                            "message.completed",
                            turn_id=request.turn.turn_id,
                            content=final_output,
                            needs_user=needs_user,
                            **request.interaction_metadata,
                        )
                        terminated = True
                        break
                if terminated:
                    break
                continue

            final_output = response.content
            turn_messages.append(LLMMessage(role="assistant", content=final_output))
            self.host.event_log.emit(
                "message.completed",
                turn_id=request.turn.turn_id,
                content=final_output,
                **request.interaction_metadata,
            )
            break
        else:
            final_output = (
                "The provider did not produce a final assistant message within "
                f"the configured step budget of {max_model_steps}."
            )
            turn_messages.append(LLMMessage(role="assistant", content=final_output))
            self.host.event_log.emit(
                "message.completed",
                turn_id=request.turn.turn_id,
                content=final_output,
                is_error=True,
                **request.interaction_metadata,
            )
        return TurnEngineResult(
            final_output=final_output,
            needs_user=needs_user,
            tool_records=tool_records,
            turn_messages=turn_messages,
            items=items,
        )

    def _append_runtime_event(self, event: RuntimeEvent) -> None:
        session_runtime = getattr(self.host, "session_runtime", None)
        control_plane = getattr(session_runtime, "control_plane", None)
        if control_plane is not None:
            control_plane.store.append([event])
