from __future__ import annotations

from collections.abc import Callable
from typing import Any

from demiurge.runtime.interactions import InteractionRuntime
from demiurge.runtime.runner import SessionTurnStepRunner


def runtime_factory_for_app(app: Any) -> Callable[[str], InteractionRuntime]:
    """Build per-conversation interaction runtimes for gateway channel adapters."""

    if not all(hasattr(app, name) for name in ("home", "version_store", "core_loader", "tool_runtime")):
        runtime = InteractionRuntime(app.runner)
        return lambda _conversation_key: runtime

    def make_runtime(_conversation_key: str) -> InteractionRuntime:
        runner = SessionTurnStepRunner(
            home=app.home,
            version_store=app.version_store,
            core_loader=app.core_loader,
            provider=app.runner.provider,
            tool_runtime=app.tool_runtime,
            core_id=app.runner.core_id,
            model_override=app.runner.model_override,
            model_resolver=app.runner.model_resolver,
            provider_name=app.runner.provider_name,
            workspace=app.runner.workspace,
            show_system_prompt=app.runner.show_system_prompt,
            runtime_timezone=app.runtime_timezone,
            task_worker=app.task_worker,
            session_runtime=app.session_runtime,
            interaction_router=app.runner.interaction_router,
            prepare_live_core=app.prepare_live_core,
        )
        return InteractionRuntime(runner)

    return make_runtime
