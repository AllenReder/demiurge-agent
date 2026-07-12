from types import SimpleNamespace

import pytest

from demiurge.app import create_app
from demiurge.channels.commands import ChannelCommandExecutor, ChannelCommandRuntime
from demiurge.runtime.conversation_lifecycle import ConversationLifecycleConfig, ConversationLifecycleRuntime
from demiurge.runtime.ingress import ConversationIngressState
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.storage import SessionRecord


def _runtime(**kwargs) -> ChannelCommandRuntime:
    return ChannelCommandRuntime(
        command_names=set(kwargs.pop("command_names", {"status"})),
        unavailable_template="Command not available: /{name}",
        unknown_template="Unknown command: /{name}",
    )


def _inbound(text: str) -> InteractionInbound:
    return InteractionInbound(
        channel="test",
        text=text,
        source="source_1",
        reply_to="reply_1",
        conversation_key="conversation_1",
        metadata={"kind": "test"},
    )


def _notice_sink(notices):
    async def send_notice(text: str) -> None:
        notices.append(text)

    return send_notice


def _state(*, runner=None, busy_mode: str = "interrupt"):
    runtime = SimpleNamespace(
        runner=runner
        or SimpleNamespace(
            core_id="assistant",
            session_id="session_1",
            provider_name="fake",
        ),
        session_runtime=None,
    )
    return ConversationIngressState(runtime=runtime, busy_mode=busy_mode, route_binding=object())


def _executor(sent, ran, **kwargs) -> ChannelCommandExecutor:
    async def send_text(inbound, text):
        sent.append((inbound.source, inbound.reply_to, text))

    async def run_inbound(_state, inbound):
        ran.append(inbound)

    lifecycle = kwargs.pop(
        "lifecycle",
        ConversationLifecycleRuntime(
            config=ConversationLifecycleConfig(
                channel=kwargs.get("channel_name", "test"),
                merge_owner_id="test:merge",
                enqueue_owner_id="test:enqueue",
            ),
            state_factory=lambda _key: _state(),
            run_turn=run_inbound,
        ),
    )
    return ChannelCommandExecutor(
        channel_name=kwargs.pop("channel_name", "test"),
        surface=kwargs.pop("surface", "text"),
        send_text=send_text,
        lifecycle=lifecycle,
        **kwargs,
    )


def _record(session_id: str) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        core_id="assistant",
        core_revision="rev_1",
        created_at="2026-07-06T03:00:00Z",
        updated_at="2026-07-06T04:00:00Z",
        channel="telegram",
        message_count=1,
    )


@pytest.mark.asyncio
async def test_channel_command_runtime_passes_non_command_through():
    notices = []
    runtime = _runtime()

    outcome = await runtime.handle(_inbound("hello"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is False
    assert outcome.inbound.text == "hello"
    assert outcome.command is None
    assert notices == []


@pytest.mark.asyncio
async def test_channel_command_runtime_rewrites_ask_command_to_ordinary_inbound():
    notices = []
    runtime = _runtime()

    outcome = await runtime.handle(_inbound("/ask explain this"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is False
    assert outcome.inbound.text == "explain this"
    assert outcome.inbound.channel == "test"
    assert outcome.inbound.source == "source_1"
    assert outcome.inbound.reply_to == "reply_1"
    assert outcome.inbound.conversation_key == "conversation_1"
    assert outcome.inbound.metadata == {"kind": "test"}
    assert outcome.command is not None
    assert outcome.command.name == "ask"
    assert notices == []


@pytest.mark.asyncio
async def test_channel_command_runtime_dispatches_available_command_handler():
    calls = []
    notices = []
    state = object()

    async def status(args, inbound, handler_state):
        calls.append((args, inbound.text, handler_state is state))

    runtime = _runtime(command_names={"status"})

    outcome = await runtime.handle(
        _inbound("/status now"),
        state,
        handlers={"status": status},
        send_notice=_notice_sink(notices),
    )

    assert outcome.handled is True
    assert calls == [("now", "/status now", True)]
    assert notices == []


@pytest.mark.asyncio
async def test_channel_command_runtime_reports_available_command_without_handler():
    notices = []
    runtime = _runtime(command_names={"status"})

    outcome = await runtime.handle(_inbound("/status"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is True
    assert notices == ["Command not available: /status"]


@pytest.mark.asyncio
async def test_channel_command_runtime_reports_unknown_command():
    notices = []
    runtime = _runtime(command_names={"status"})

    outcome = await runtime.handle(_inbound("/missing"), object(), handlers={}, send_notice=_notice_sink(notices))

    assert outcome.handled is True
    assert notices == ["Unknown command: /missing"]


@pytest.mark.asyncio
async def test_channel_command_executor_sets_busy_mode_through_shared_handler():
    sent = []
    ran = []
    executor = _executor(sent, ran)
    state = _state()

    await executor.handlers()["busy"]("queue", _inbound("/busy queue"), state)

    assert state.busy_mode == "queue"
    assert sent == [("source_1", "reply_1", "Busy mode: `queue`")]
    assert ran == []


@pytest.mark.asyncio
async def test_channel_command_executor_queue_runs_prompt_as_inbound():
    sent = []
    ran = []
    executor = _executor(sent, ran)
    state = _state()

    await executor.handlers()["queue"]("queued prompt", _inbound("/queue queued prompt"), state)
    if state.active_task is not None:
        await state.active_task

    assert [inbound.text for inbound in ran] == ["queued prompt"]
    assert sent == [("source_1", "reply_1", "Queued: queued prompt")]


@pytest.mark.asyncio
async def test_channel_command_executor_formats_status_with_adapter_extras():
    sent = []
    ran = []
    executor = _executor(
        sent,
        ran,
        surface="telegram",
        channel_name="telegram",
        include_status_channel=False,
        status_extra_lines=lambda _inbound: ("- access: `restricted`",),
    )
    state = _state(busy_mode="queue")

    await executor.handlers()["status"]("", _inbound("/status"), state)

    text = sent[0][2]
    assert "# Status" in text
    assert "- channel:" not in text
    assert "- core: `assistant`" in text
    assert "- busy mode: `queue`" in text
    assert "- access: `restricted`" in text


@pytest.mark.asyncio
async def test_channel_command_executor_resume_rebinds_current_conversation():
    sent = []
    ran = []

    class Runner:
        core_id = "assistant"
        session_id = "session_1"
        provider_name = "fake"
        interaction_router = object()

        def __init__(self):
            self.resume_calls = []
            self.principal_scope = object()

        async def resolve_command_principal_scope(self, inbound):
            return self.principal_scope

        def resume_session(self, session_id: str, **kwargs):
            self.resume_calls.append({"session_id": session_id, **kwargs})

    class RouteBinding:
        def __init__(self):
            self.binds = []

        def bind(self, router, session_id: str):
            self.binds.append((router, session_id))

    runner = Runner()
    route_binding = RouteBinding()

    def list_owned_sessions(scope, *, core_id, limit):
        assert scope is runner.principal_scope
        return [_record("session_1"), _record("session_2")]

    def get_owned_session(scope, session_id):
        assert scope is runner.principal_scope
        return _record(session_id)

    state = ConversationIngressState(
        runtime=SimpleNamespace(
            runner=runner,
            session_runtime=SimpleNamespace(
                list_owned_sessions=list_owned_sessions,
                get_owned_session=get_owned_session,
            ),
        ),
        busy_mode="interrupt",
        route_binding=route_binding,
    )
    executor = _executor(sent, ran, channel_name="telegram")

    await executor.handlers()["resume"]("2", _inbound("/resume 2"), state)

    assert runner.resume_calls == [
        {
            "session_id": "session_2",
            "channel": "telegram",
            "conversation_key": "conversation_1",
            "source": "source_1",
            "reply_to": "reply_1",
            "replace_conversation_binding": True,
        }
    ]
    assert route_binding.binds == [(runner.interaction_router, "session_2")]
    assert sent == [("source_1", "reply_1", "Resumed session: `session_2`")]


@pytest.mark.asyncio
async def test_channel_sessions_list_uses_principal_owned_sessions():
    sent = []
    ran = []
    resolved_scope = object()

    class SessionRuntime:
        def list_sessions(self, *, core_id, limit):
            return [_record("session_1"), _record("session_2")]

        def list_owned_sessions(self, scope, *, core_id, limit):
            assert scope is resolved_scope
            return [_record("session_1")]

    class Runner:
        core_id = "assistant"
        session_id = "session_1"
        provider_name = "fake"
        principal_scope = None

        async def resolve_command_principal_scope(self, inbound):
            assert inbound.conversation_key == "conversation_1"
            return resolved_scope

    runner = Runner()
    state = ConversationIngressState(
        runtime=SimpleNamespace(
            runner=runner,
            session_runtime=SessionRuntime(),
        ),
        busy_mode="interrupt",
        route_binding=SimpleNamespace(bind=lambda *_args: None),
    )
    executor = _executor(sent, ran, channel_name="telegram")

    await executor.handlers()["sessions"]("", _inbound("/sessions"), state)

    assert "session_1" in sent[0][2]
    assert "session_2" not in sent[0][2]


@pytest.mark.asyncio
async def test_channel_sessions_derives_scope_from_authenticated_inbound(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    resolver = PrincipalScopeResolver(app.runtime_store)
    session_ids = {}
    for suffix in ("a", "b"):
        session_id = f"session_real_command_{suffix}"
        conversation_key = f"test:conversation:{suffix}"
        principal_key = f"user_{suffix}"
        issued = resolver.issue_conversation(
            channel="test",
            principal_key=principal_key,
            conversation_key=conversation_key,
            session_id=session_id,
        )
        app.session_runtime.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            channel="test",
            conversation_key=conversation_key,
            principal_scope=issued,
        )
        session_ids[suffix] = session_id

    sent = []
    ran = []
    state = ConversationIngressState(
        runtime=SimpleNamespace(
            runner=app.runner,
            session_runtime=app.session_runtime,
        ),
        busy_mode="interrupt",
        route_binding=SimpleNamespace(bind=lambda *_args: None),
    )
    executor = _executor(sent, ran, channel_name="test")
    inbound = InteractionInbound(
        channel="test",
        text="/sessions",
        source="user_a",
        principal_key="user_a",
        reply_to="reply_a",
        conversation_key="test:conversation:a",
        metadata={"kind": "test"},
    )

    try:
        await executor.handlers()["sessions"]("", inbound, state)

        assert session_ids["a"] in sent[0][2]
        assert session_ids["b"] not in sent[0][2]
        assert app.runner.session_id == session_ids["a"]
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_channel_resume_rejects_unowned_raw_session_id_before_runner_call():
    sent = []
    ran = []
    principal_scope = object()

    class Runner:
        core_id = "assistant"
        session_id = "session_1"
        provider_name = "fake"
        interaction_router = object()

        def __init__(self):
            self.resume_calls = []
            self.principal_scope = principal_scope

        async def resolve_command_principal_scope(self, inbound):
            return self.principal_scope

        def resume_session(self, session_id, **kwargs):
            self.resume_calls.append({"session_id": session_id, **kwargs})

    class SessionRuntime:
        def list_owned_sessions(self, scope, *, core_id, limit):
            assert scope is principal_scope
            return [_record("session_1")]

        def get_owned_session(self, scope, session_id):
            assert scope is principal_scope
            raise FileNotFoundError(f"session not found: {session_id}")

    runner = Runner()
    state = ConversationIngressState(
        runtime=SimpleNamespace(
            runner=runner,
            session_runtime=SessionRuntime(),
        ),
        busy_mode="interrupt",
        route_binding=SimpleNamespace(bind=lambda *_args: None),
    )
    executor = _executor(sent, ran, channel_name="telegram")

    await executor.handlers()["resume"](
        "session_2",
        _inbound("/resume session_2"),
        state,
    )

    assert runner.resume_calls == []
    assert sent == [
        (
            "source_1",
            "reply_1",
            "Session not found or not authorized.",
        )
    ]


@pytest.mark.asyncio
async def test_channel_command_executor_lists_tools_from_runner_registry():
    sent = []
    ran = []
    core = SimpleNamespace(core_id="assistant")
    tool = SimpleNamespace(name="tools_list", source="builtin", approval_policy="never")

    class Runner:
        core_id = "assistant"
        session_id = "session_1"
        provider_name = "fake"

        def __init__(self):
                self.tool_runtime = SimpleNamespace(
                    registry_for=lambda loaded_core, *, turn=None: [tool]
                )

        async def load_active_core(self):
            return core

    executor = _executor(sent, ran)
    state = _state(runner=Runner())

    await executor.handlers()["tools"]("", _inbound("/tools"), state)

    assert sent == [("source_1", "reply_1", "# Tools\n- `tools_list` - builtin - never")]
