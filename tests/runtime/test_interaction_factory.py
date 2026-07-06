from __future__ import annotations

from types import SimpleNamespace

from demiurge.runtime.interaction_factory import runtime_factory_for_app


def test_runtime_factory_for_app_reuses_minimal_app_runner():
    runner = SimpleNamespace(session_id="session_1")
    app = SimpleNamespace(runner=runner)

    factory = runtime_factory_for_app(app)

    first = factory("conversation-a")
    second = factory("conversation-b")
    assert first is second
    assert first.runner is runner
    assert getattr(runner, "interaction_router", None) is first.router


def test_runtime_factory_for_app_builds_full_app_runner(monkeypatch):
    created = []

    class FakeRunner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.session_id = f"session_{len(created) + 1}"
            created.append(self)

    monkeypatch.setattr("demiurge.runtime.interaction_factory.SessionTurnStepRunner", FakeRunner)
    router = object()
    source_runner = SimpleNamespace(
        provider="provider",
        core_id="core",
        model_override="model",
        model_resolver="resolver",
        provider_name="provider_name",
        workspace="workspace",
        show_system_prompt=True,
        interaction_router=router,
    )
    app = SimpleNamespace(
        home="home",
        version_store="versions",
        core_loader="loader",
        tool_runtime="tools",
        runner=source_runner,
        runtime_timezone="timezone",
        task_worker="tasks",
        session_runtime="sessions",
        prepare_live_core="prepare",
    )

    factory = runtime_factory_for_app(app)
    first = factory("conversation-a")
    second = factory("conversation-b")

    assert first is not second
    assert [runtime.runner for runtime in (first, second)] == created
    assert created[0].kwargs == {
        "home": "home",
        "version_store": "versions",
        "core_loader": "loader",
        "provider": "provider",
        "tool_runtime": "tools",
        "core_id": "core",
        "model_override": "model",
        "model_resolver": "resolver",
        "provider_name": "provider_name",
        "workspace": "workspace",
        "show_system_prompt": True,
        "runtime_timezone": "timezone",
        "task_worker": "tasks",
        "session_runtime": "sessions",
        "interaction_router": router,
        "prepare_live_core": "prepare",
    }
    assert created[1].kwargs == created[0].kwargs
