from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

from demiurge.core import LoadedCore, SlotDefinition
from demiurge.providers import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolDefinition
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.store import RuntimeEvent
from demiurge.sdk import ContextContribution, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityFacade
from demiurge.security.redaction import (
    REDACTION_FAILED,
    RedactionView,
    SecretRedactor,
    redact_tool_result,
)
from demiurge.tools.records import ToolExecutionRecord
from demiurge.tools.registry import EffectRequest, EffectResult, ResolvedEffectCatalog

if TYPE_CHECKING:
    from demiurge.runtime.turn_pipeline import TurnExecutionContext


@dataclass(slots=True)
class TurnEngineRequest:
    core: LoadedCore
    turn: TurnContext
    capability: CapabilityFacade
    execution_context: TurnExecutionContext
    context: list[ContextContribution]
    available_tools: list[ToolDefinition]
    effect_catalog: ResolvedEffectCatalog
    interaction_metadata: dict[str, Any]
    use_bootstrap_context: bool = True


@dataclass(slots=True)
class TurnEngineResult:
    final_output: str
    needs_user: bool = False
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)
    turn_messages: list[LLMMessage] = field(default_factory=list)
    items: list[InteractionItem] = field(default_factory=list)


def _resolved_effect_event_fields(
    catalog: ResolvedEffectCatalog | None,
    *,
    tool_name: str,
    fallback_core_revision: str,
) -> dict[str, str | None]:
    entry = catalog.entry_for(tool_name) if catalog is not None else None
    return {
        "effect_source": entry.source if entry is not None else None,
        "effect_provenance": entry.provenance if entry is not None else None,
        "core_revision": (
            entry.core_revision
            if entry is not None
            else fallback_core_revision
        ),
    }


class TurnEngineHost(Protocol):
    """Host operations the model/tool loop needs for one turn."""

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def build_messages(
        self,
        core: LoadedCore,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        session_id: str,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool,
    ) -> list[LLMMessage]:
        ...

    async def deliver_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        ...

    def resolve_model_name(self, core: LoadedCore) -> str:
        ...

    async def complete_provider(self, request: LLMRequest) -> LLMResponse:
        ...

    async def send_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ) -> tuple[Any, list[InteractionItem]]:
        ...

    async def send_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        ...

    async def execute_tool(
        self,
        request: EffectRequest,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        execution_context: TurnExecutionContext,
        output_factory: Callable[[SlotDefinition], Any],
    ) -> EffectResult:
        ...

    def output_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        capability: CapabilityFacade,
        interaction_metadata: dict[str, Any],
        items: list[InteractionItem],
    ) -> Any:
        ...

    async def send_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        model_result: ToolResult,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        ...

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        ...

    def tool_result_model_content(self, result: ToolResult) -> str:
        ...

    def truncate_model_content(self, content: str) -> str:
        ...


class RunnerTurnEngineHost:
    """Adapter from SessionTurnStepRunner to TurnEngineHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.emit_turn_event(event_type, **payload)

    def build_messages(
        self,
        core: LoadedCore,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        session_id: str,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool,
    ) -> list[LLMMessage]:
        return self.runner.build_turn_messages(
            core,
            context,
            turn_messages,
            session_id=session_id,
            turn_id=turn_id,
            step_id=step_id,
            use_bootstrap_context=use_bootstrap_context,
        )

    async def deliver_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.runner.deliver_turn_system_prompt_debug(
            messages,
            turn=turn,
            step_id=step_id,
            interaction_metadata=interaction_metadata,
        )

    def resolve_model_name(self, core: LoadedCore) -> str:
        return self.runner.resolve_turn_model_name(core)

    async def complete_provider(self, request: LLMRequest) -> LLMResponse:
        return await self.runner.complete_turn_provider(request)

    async def send_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ) -> tuple[Any, list[InteractionItem]]:
        return await self.runner.runtime_io.send_assistant_step(
            turn=turn,
            step_id=step_id,
            content=content,
            tool_calls=tool_calls,
            interaction_metadata=interaction_metadata,
        )

    async def send_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return await self.runner.runtime_io.send_tool_call_started(
            turn=turn,
            step_id=step_id,
            call=call,
            interaction_metadata=interaction_metadata,
        )

    async def execute_tool(
        self,
        request: EffectRequest,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        execution_context: TurnExecutionContext,
        output_factory: Callable[[SlotDefinition], Any],
    ) -> EffectResult:
        return await self.runner.execute_turn_tool(
            request,
            core=core,
            turn=turn,
            capability=capability,
            execution_context=execution_context,
            output_factory=output_factory,
        )

    def output_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        capability: CapabilityFacade,
        interaction_metadata: dict[str, Any],
        items: list[InteractionItem],
    ) -> Any:
        return self.runner.slot_context.module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            items=items,
        )

    async def send_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        model_result: ToolResult,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return await self.runner.runtime_io.send_tool_call_finished(
            turn=turn,
            step_id=step_id,
            record=record,
            model_result=model_result,
            interaction_metadata=interaction_metadata,
        )

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        self.runner.append_turn_runtime_event(event)

    def tool_result_model_content(self, result: ToolResult) -> str:
        return self.runner.turn_tool_result_model_content(result)

    def truncate_model_content(self, content: str) -> str:
        return self.runner.truncate_turn_model_content(content)


class TurnEngine:
    """Runs the foreground provider/tool loop for one agent turn."""

    def __init__(self, host: TurnEngineHost):
        self.host = host

    async def run(self, request: TurnEngineRequest) -> TurnEngineResult:
        turn_messages: list[LLMMessage] = [LLMMessage(role="user", content=request.turn.user_input.content)]
        tool_records: list[ToolExecutionRecord] = []
        items: list[InteractionItem] = []
        final_output = ""
        needs_user = False
        turn_redactor: SecretRedactor | None = SecretRedactor()
        max_model_steps = request.core.manifest.runtime.max_model_steps
        for step_index in range(1, max_model_steps + 1):
            step_id = f"{request.turn.turn_id}_step_{step_index}"
            self.host.emit_event(
                "step.started",
                turn_id=request.turn.turn_id,
                step_id=step_id,
                tools=[tool.name for tool in request.available_tools],
                **request.interaction_metadata,
            )
            messages = self.host.build_messages(
                request.core,
                request.context,
                turn_messages,
                session_id=request.turn.session_id,
                turn_id=request.turn.turn_id,
                step_id=step_id,
                use_bootstrap_context=request.use_bootstrap_context,
            )
            await self.host.deliver_system_prompt_debug(
                messages,
                turn=request.turn,
                step_id=step_id,
                interaction_metadata=request.interaction_metadata,
            )
            provider_request = LLMRequest(
                model=self.host.resolve_model_name(request.core),
                messages=messages,
                tools=request.available_tools,
                metadata={"turn_id": request.turn.turn_id, "step_id": step_id},
            )
            response = await self.host.complete_provider(provider_request)
            if response.tool_calls:
                call_redactor = _tool_call_redactor(
                    response.tool_calls,
                    base=turn_redactor,
                )
                call_redactor = _extended_redactor(
                    call_redactor,
                    response.content,
                )
                turn_redactor = call_redactor
                model_content = _redacted_text(
                    response.content,
                    redactor=call_redactor,
                    view=RedactionView.MODEL,
                )
                call_views = [
                    (
                        call,
                        _redacted_tool_call(
                            call,
                            redactor=call_redactor,
                            view=RedactionView.MODEL,
                        ),
                        _redacted_tool_call(
                            call,
                            redactor=call_redactor,
                            view=RedactionView.OPERATOR,
                        ),
                        _redacted_tool_call(
                            call,
                            redactor=call_redactor,
                            view=RedactionView.EVENT,
                        ),
                        _redacted_tool_call(
                            call,
                            redactor=call_redactor,
                            view=RedactionView.DURABLE,
                        ),
                    )
                    for call in response.tool_calls
                ]
                turn_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=model_content,
                        tool_calls=[
                            model_call
                            for _, model_call, _, _, _ in call_views
                        ],
                        persist=False,
                    )
                )
                _, interim_items = await self.host.send_assistant_step(
                    turn=request.turn,
                    step_id=step_id,
                    content=model_content,
                    tool_calls=[model_call for _, model_call, _, _, _ in call_views],
                    interaction_metadata=request.interaction_metadata,
                )
                items.extend(interim_items)
                self.host.emit_event(
                    "actions.requested",
                    turn_id=request.turn.turn_id,
                    step_id=step_id,
                    actions=[
                        asdict(event_call)
                        for _, _, _, event_call, _ in call_views
                    ],
                    **request.interaction_metadata,
                )
                for call, _, operator_call, _, durable_call in call_views:
                    items.append(
                        await self.host.send_tool_call_started(
                            turn=request.turn,
                            step_id=step_id,
                            call=operator_call,
                            interaction_metadata=request.interaction_metadata,
                        )
                    )
                    self.host.append_runtime_event(
                        RuntimeEvent(
                            type="tool.call.started",
                            aggregate_type="tool_call",
                            aggregate_id=call.id,
                            payload={
                                "turn_id": request.turn.turn_id,
                                "step_id": step_id,
                                "tool_name": call.name,
                                "status": "running",
                                "args": dict(durable_call.arguments),
                                **_resolved_effect_event_fields(
                                    request.effect_catalog,
                                    tool_name=call.name,
                                    fallback_core_revision=request.turn.core_revision,
                                ),
                            },
                        )
                    )
                terminated = False
                for call, _, operator_call, _, _ in call_views:
                    tool_items: list[InteractionItem] = []
                    effect_request = request.effect_catalog.request_for(call)
                    if effect_request is None:
                        effect_result = EffectResult.not_found(
                            name=call.name,
                            core_id=request.turn.core_id,
                            core_revision=request.turn.core_revision,
                        )
                    else:
                        effect_result = await self.host.execute_tool(
                            effect_request,
                            core=request.core,
                            turn=request.turn,
                            capability=request.capability,
                            execution_context=request.execution_context,
                            output_factory=lambda slot: self.host.output_client(
                                slot,
                                turn=request.turn,
                                capability=request.capability,
                                interaction_metadata=request.interaction_metadata,
                                items=tool_items,
                            ),
                        )
                    model_result = _safe_effect_view(
                        effect_result,
                        redactor=turn_redactor,
                        view=RedactionView.MODEL,
                    )
                    operator_result = _safe_effect_view(
                        effect_result,
                        redactor=turn_redactor,
                        view=RedactionView.OPERATOR,
                    )
                    event_result = _safe_effect_view(
                        effect_result,
                        redactor=turn_redactor,
                        view=RedactionView.EVENT,
                    )
                    durable_result = _safe_effect_view(
                        effect_result,
                        redactor=turn_redactor,
                        view=RedactionView.DURABLE,
                    )
                    self.host.append_runtime_event(
                        RuntimeEvent(
                            type=(
                                "tool.call.failed"
                                if durable_result.is_error
                                else "tool.call.completed"
                            ),
                            aggregate_type="tool_call",
                            aggregate_id=call.id,
                            payload={
                                "turn_id": request.turn.turn_id,
                                "step_id": step_id,
                                "tool_name": call.name,
                                "status": (
                                    "failed"
                                    if durable_result.is_error
                                    else "succeeded"
                                ),
                                "effect_status": (
                                    effect_result.status
                                ),
                                "effect_error": (
                                    _effect_error_view(
                                        effect_result,
                                        durable_result,
                                    )
                                    if effect_result.error is not None
                                    else None
                                ),
                                **_resolved_effect_event_fields(
                                    request.effect_catalog,
                                    tool_name=call.name,
                                    fallback_core_revision=request.turn.core_revision,
                                ),
                                "result": {
                                    "content": durable_result.content,
                                    "data": durable_result.data,
                                    "is_error": durable_result.is_error,
                                    "terminate": durable_result.terminate,
                                },
                            },
                        )
                    )
                    items.extend(tool_items)
                    record = ToolExecutionRecord(
                        call=operator_call,
                        result=operator_result,
                    )
                    tool_records.append(record)
                    turn_messages.append(
                        LLMMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.id,
                            content=self.host.truncate_model_content(
                                self.host.tool_result_model_content(model_result)
                            ),
                            persist=False,
                        )
                    )
                    self.host.emit_event(
                        "action.result",
                        turn_id=request.turn.turn_id,
                        step_id=step_id,
                        tool_name=call.name,
                        tool_call_id=call.id,
                        content=event_result.content,
                        model_output=event_result.model_output,
                        display_output=event_result.display_output,
                        data=event_result.data,
                        is_error=event_result.is_error,
                        terminate=event_result.terminate,
                        **request.interaction_metadata,
                    )
                    items.append(
                        await self.host.send_tool_call_finished(
                            turn=request.turn,
                            step_id=step_id,
                            record=record,
                            model_result=model_result,
                            interaction_metadata=request.interaction_metadata,
                        )
                    )
                    if operator_result.terminate:
                        final_output = operator_result.content
                        needs_user = bool(
                            isinstance(operator_result.data, dict)
                            and operator_result.data.get("needs_user")
                        )
                        turn_messages.append(LLMMessage(role="assistant", content=final_output))
                        self.host.emit_event(
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

            turn_redactor = _extended_redactor(
                turn_redactor,
                response.content,
            )
            final_output = _redacted_text(
                response.content,
                redactor=turn_redactor,
                view=RedactionView.MODEL,
            )
            turn_messages.append(LLMMessage(role="assistant", content=final_output))
            self.host.emit_event(
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
            self.host.emit_event(
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


def _redacted_tool_call(
    call: ToolCall,
    *,
    redactor: SecretRedactor | None,
    view: RedactionView,
) -> ToolCall:
    payload = {"arguments": dict(call.arguments)}
    result = (
        redactor.redact(payload, view=view)
        if redactor is not None
        else None
    )
    arguments = (
        result.value.get("arguments")
        if result is not None and isinstance(result.value, dict)
        else None
    )
    if result is None or result.failed or not isinstance(arguments, dict):
        arguments = {"redaction": REDACTION_FAILED}
    return ToolCall(
        name=call.name,
        arguments=arguments,
        id=call.id,
    )


def _redacted_text(
    value: str,
    *,
    redactor: SecretRedactor | None,
    view: RedactionView,
) -> str:
    if redactor is None:
        return REDACTION_FAILED
    result = redactor.redact(value, view=view)
    if result.failed or not isinstance(result.value, str):
        return REDACTION_FAILED
    return result.value


def _tool_call_redactor(
    calls: list[ToolCall],
    *,
    base: SecretRedactor | None,
) -> SecretRedactor | None:
    if base is None:
        return None
    try:
        secrets = list(base.secrets)
        for call in calls:
            secrets.extend(
                SecretRedactor.from_value(
                    {"arguments": dict(call.arguments)}
                ).secrets
            )
        return SecretRedactor(secrets)
    except Exception:
        return None


def _extended_redactor(
    redactor: SecretRedactor | None,
    value: Any,
) -> SecretRedactor | None:
    if redactor is None:
        return None
    try:
        return redactor.with_value(value)
    except Exception:
        return None


def _safe_effect_view(
    effect_result: EffectResult,
    *,
    redactor: SecretRedactor | None,
    view: RedactionView,
) -> ToolResult:
    if redactor is None:
        return ToolResult(
            content=REDACTION_FAILED,
            data={"redactionFailed": True},
            is_error=True,
            model_output=REDACTION_FAILED,
            display_output=REDACTION_FAILED,
        )
    result, _ = redact_tool_result(
        effect_result.to_tool_result(view),
        redactor=redactor,
        view=view,
    )
    return result


def _effect_error_view(
    effect_result: EffectResult,
    result: ToolResult,
) -> dict[str, Any]:
    assert effect_result.error is not None
    return {
        "code": effect_result.error.code,
        "message": result.content,
        "execution_started": effect_result.error.execution_started,
        "provenance": effect_result.error.provenance,
    }
