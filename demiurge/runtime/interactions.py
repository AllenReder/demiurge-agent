from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from uuid import uuid4

from demiurge.providers import ToolCall
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord


@dataclass(slots=True)
class InteractionInbound:
    channel: str
    text: str
    source: str
    reply_to: str | None = None
    conversation_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class InteractionDelivery:
    type: str = "text"
    kind: str = "message"
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    fallback_text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    visible: bool = True
    history_policy: str = "persist"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolInteractionRecord:
    call: ToolCall
    phase: str
    status: str
    result: ToolResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def started(cls, call: ToolCall, *, metadata: dict[str, Any] | None = None) -> "ToolInteractionRecord":
        return cls(call=call, phase="start", status="running", metadata=dict(metadata or {}))

    @classmethod
    def finished(cls, record: ToolExecutionRecord, *, metadata: dict[str, Any] | None = None) -> "ToolInteractionRecord":
        return cls(
            call=record.call,
            phase="finish",
            status="error" if record.result.is_error else "ok",
            result=record.result,
            metadata=dict(metadata or {}),
        )

    def execution_record(self) -> ToolExecutionRecord | None:
        if self.result is None:
            return None
        return ToolExecutionRecord(call=self.call, result=self.result)


@dataclass(slots=True)
class InteractionItem:
    kind: str
    delivery: InteractionDelivery | None = None
    tool_result: ToolExecutionRecord | None = None
    tool_call: ToolInteractionRecord | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    dispatch_status: str = "pending"

    @classmethod
    def delivery_item(cls, delivery: InteractionDelivery) -> "InteractionItem":
        metadata = dict(delivery.metadata)
        status = str(metadata.get("dispatch_status") or metadata.get("delivery_status") or "pending")
        metadata["dispatch_status"] = status
        return cls(kind="delivery", delivery=delivery, metadata=metadata, dispatch_status=status)

    @classmethod
    def tool_result_item(cls, record: ToolExecutionRecord, *, metadata: dict[str, Any] | None = None) -> "InteractionItem":
        item_metadata = dict(metadata or {})
        status = str(item_metadata.get("dispatch_status") or "pending")
        item_metadata["dispatch_status"] = status
        return cls(kind="tool_result", tool_result=record, metadata=item_metadata, dispatch_status=status)

    @classmethod
    def tool_call_item(cls, record: ToolInteractionRecord, *, metadata: dict[str, Any] | None = None) -> "InteractionItem":
        item_metadata = dict(record.metadata)
        item_metadata.update(dict(metadata or {}))
        status = str(item_metadata.get("dispatch_status") or "pending")
        item_metadata["dispatch_status"] = status
        return cls(kind="tool_call", tool_call=record, metadata=item_metadata, dispatch_status=status)

    def set_dispatch_status(self, status: str) -> None:
        self.dispatch_status = status
        self.metadata["dispatch_status"] = status
        if self.delivery is not None:
            delivery_metadata = dict(self.delivery.metadata)
            delivery_metadata["delivery_status"] = status
            self.delivery.metadata = delivery_metadata


@dataclass(slots=True)
class UserPromptRequest:
    question: str
    choices: list[str] = field(default_factory=list)
    session_id: str | None = None
    turn_id: str | None = None
    conversation_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, init=False)
class InteractionOutbound:
    channel: str
    items: list[InteractionItem]
    prompt: UserPromptRequest | None = None
    session_id: str
    turn_id: str | None = None
    metadata: dict[str, Any]
    on_delivered: Callable[[], None] | None = None

    def __init__(
        self,
        channel: str,
        *,
        session_id: str,
        items: list[InteractionItem] | None = None,
        prompt: UserPromptRequest | None = None,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        on_delivered: Callable[[], None] | None = None,
    ) -> None:
        if not session_id:
            raise ValueError("InteractionOutbound.session_id is required")
        self.channel = channel
        self.items = list(items or [])
        self.prompt = prompt
        self.session_id = session_id
        self.turn_id = turn_id
        self.metadata = dict(metadata or {})
        self.on_delivered = on_delivered

    @property
    def deliveries(self) -> list[InteractionDelivery]:
        return [item.delivery for item in self.items if item.kind == "delivery" and item.delivery is not None]

    @property
    def tool_results(self) -> list[ToolExecutionRecord]:
        results: list[ToolExecutionRecord] = []
        for item in self.items:
            if item.kind == "tool_result" and item.tool_result is not None:
                results.append(item.tool_result)
                continue
            if item.kind == "tool_call" and item.tool_call is not None:
                record = item.tool_call.execution_record()
                if record is not None:
                    results.append(record)
        return results

    @property
    def tool_calls(self) -> list[ToolInteractionRecord]:
        return [item.tool_call for item in self.items if item.kind == "tool_call" and item.tool_call is not None]

    def mark_delivered(self) -> None:
        for item in self.items:
            item.set_dispatch_status("delivered")
        callback = self.on_delivered
        self.on_delivered = None
        if callback is not None:
            callback()

    def mark_unrouted(self) -> None:
        for item in self.items:
            item.set_dispatch_status("unrouted")


class SessionInteractionRoute(Protocol):
    async def deliver(self, outbound: InteractionOutbound) -> None:
        ...

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        ...

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        ...


@dataclass(frozen=True, slots=True)
class SessionRouteToken:
    token_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class SessionRouteDeliveryResult:
    status: str


@dataclass(slots=True)
class SessionRouteBinding:
    route: SessionInteractionRoute
    token: SessionRouteToken | None = None

    def bind(self, router: "SessionInteractionRouter", session_id: str) -> SessionRouteToken:
        if self.token is not None:
            if self.token.session_id == session_id and router.is_bound(self.token):
                return self.token
            router.unbind(self.token)
        self.token = router.bind(session_id, self.route)
        return self.token

    def unbind(self, router: "SessionInteractionRouter") -> None:
        if self.token is not None:
            router.unbind(self.token)
            self.token = None


@dataclass(slots=True)
class _BoundSessionRoute:
    session_id: str
    route: SessionInteractionRoute

    async def deliver(self, outbound: InteractionOutbound) -> None:
        if outbound.session_id != self.session_id:
            raise RuntimeError(
                f"route bound to session `{self.session_id}` received outbound for `{outbound.session_id}`"
            )
        value = self.route.deliver(outbound)
        if inspect.isawaitable(value):
            await value

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        if prompt.session_id and prompt.session_id != self.session_id:
            raise RuntimeError(f"route bound to session `{self.session_id}` received prompt for `{prompt.session_id}`")
        value = self.route.prompt_user(prompt)
        if inspect.isawaitable(value):
            return await value
        return str(value)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        request_session_id = getattr(request, "session_id", None)
        if request_session_id and request_session_id != self.session_id:
            raise RuntimeError(
                f"route bound to session `{self.session_id}` received approval for `{request_session_id}`"
            )
        value = self.route.request_approval(request)
        if inspect.isawaitable(value):
            return await value
        return value


class SessionInteractionRouter:
    """Routes live interaction effects to the adapter bound for a session."""

    def __init__(self) -> None:
        self._routes: dict[str, dict[str, _BoundSessionRoute]] = {}

    def bind(self, session_id: str, route: SessionInteractionRoute) -> SessionRouteToken:
        if not session_id:
            raise ValueError("session_id is required")
        token = SessionRouteToken(token_id=f"route_{uuid4().hex}", session_id=session_id)
        self._routes.setdefault(session_id, {})[token.token_id] = _BoundSessionRoute(session_id=session_id, route=route)
        return token

    def unbind(self, token: SessionRouteToken) -> None:
        routes = self._routes.get(token.session_id)
        if not routes:
            return
        routes.pop(token.token_id, None)
        if not routes:
            self._routes.pop(token.session_id, None)

    def is_bound(self, token: SessionRouteToken) -> bool:
        return token.token_id in self._routes.get(token.session_id, {})

    def route_for(self, session_id: str | None) -> _BoundSessionRoute | None:
        if not session_id:
            return None
        routes = self._routes.get(session_id)
        if not routes:
            return None
        return next(reversed(routes.values()))

    async def deliver(self, outbound: InteractionOutbound) -> SessionRouteDeliveryResult:
        route = self.route_for(outbound.session_id)
        if route is None:
            outbound.mark_unrouted()
            return SessionRouteDeliveryResult(status="unrouted")
        await route.deliver(outbound)
        outbound.mark_delivered()
        return SessionRouteDeliveryResult(status="delivered")

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        route = self.route_for(prompt.session_id)
        if route is None:
            return ""
        return await route.prompt_user(prompt)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        session_id = getattr(request, "session_id", None)
        route = self.route_for(session_id)
        if route is None:
            return ApprovalDecision("deny", "no_interactive_route")
        return await route.request_approval(request)


class BridgeApprovalProvider:
    name = "session_interaction_router"

    def __init__(self, router: SessionInteractionRouter):
        self.router = router

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        decision = self.router.request_approval(request)
        if inspect.isawaitable(decision):
            decision = await decision
        return decision


class InteractionExecutionRuntime:
    """Runs the foreground turn for one inbound interaction."""

    def __init__(self, runner):
        self.runner = runner

    async def run(
        self,
        inbound: InteractionInbound,
        *,
        route_binding: SessionRouteBinding | None = None,
        route: SessionInteractionRoute | None = None,
    ):
        if route_binding is None and route is not None:
            route_binding = SessionRouteBinding(route=route)
        result = await self.runner.run_turn(inbound.text, interaction=inbound, route_binding=route_binding)
        background_tasks = getattr(self.runner, "background_tasks", None)
        if background_tasks is not None:
            await background_tasks.drain(include_runtime_tasks=False)
        return result


class InteractionResponseRuntime:
    """Projects a completed turn into adapter-facing interaction output."""

    def build(self, result, inbound: InteractionInbound) -> InteractionOutbound:
        return InteractionOutbound(
            channel=inbound.channel,
            session_id=result.session_id,
            items=self.pending_items(result),
            prompt=self.prompt_from_result(result, inbound),
            turn_id=result.turn_id,
            metadata=self.outbound_metadata(inbound),
        )

    def pending_items(self, result) -> list[InteractionItem]:
        return [item for item in result.items if item.dispatch_status == "pending"]

    def outbound_metadata(self, inbound: InteractionInbound) -> dict[str, Any]:
        return {
            "source": inbound.source,
            "reply_to": inbound.reply_to,
            "conversation_key": inbound.conversation_key,
            **dict(inbound.metadata or {}),
        }

    def prompt_from_result(self, result, inbound: InteractionInbound) -> UserPromptRequest | None:
        if not result.needs_user:
            return None
        for record in reversed(result.tool_results):
            data = record.result.data
            if not isinstance(data, dict) or not data.get("needs_user"):
                continue
            question = str(data.get("question") or record.result.content or "").strip()
            choices = data.get("choices")
            return UserPromptRequest(
                question=question,
                choices=[str(choice) for choice in choices] if isinstance(choices, list) else [],
                session_id=result.session_id,
                turn_id=result.turn_id,
                conversation_key=inbound.conversation_key,
                metadata={
                    "channel": inbound.channel,
                    "source": inbound.source,
                    "reply_to": inbound.reply_to,
                    **dict(inbound.metadata or {}),
                },
            )
        return None


class InteractionRuntime:
    def __init__(self, runner, *, router: SessionInteractionRouter | None = None):
        self.runner = runner
        self.router = router or getattr(runner, "interaction_router", None) or SessionInteractionRouter()
        setattr(runner, "interaction_router", self.router)
        self.execution = InteractionExecutionRuntime(runner)
        self.response = InteractionResponseRuntime()

    async def handle(
        self,
        inbound: InteractionInbound,
        *,
        route_binding: SessionRouteBinding | None = None,
        route: SessionInteractionRoute | None = None,
    ) -> InteractionOutbound:
        result = await self.execution.run(
            inbound,
            route_binding=route_binding,
            route=route,
        )
        return self.response.build(result, inbound)
