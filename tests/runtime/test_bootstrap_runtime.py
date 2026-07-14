from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore, PhasePipeline, SlotDefinition
from demiurge.runtime.bootstrap import BootstrapSlotRequest, BootstrapSlotRuntime
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slots import SlotRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.security.capabilities import CapabilityFacade


def _slot(tmp_path: Path, slot_id: str, code: str, *, failure_policy: str = "soft") -> SlotDefinition:
    core_root = tmp_path / "core"
    slot_root = core_root / "agent" / "bootstrap" / slot_id
    slot_root.mkdir(parents=True, exist_ok=True)
    (slot_root / "module.py").write_text(code, encoding="utf-8")
    return SlotDefinition(
        kind="bootstrap",
        slot_id=slot_id,
        path=slot_root,
        relative_path=f"agent/bootstrap/{slot_id}",
        manifest={},
        core_root=core_root,
        entrypoint="module:process",
        failure_policy=failure_policy,
    )


def _core(tmp_path: Path, *, slots: list[SlotDefinition], enabled: bool = True) -> LoadedCore:
    manifest = CoreManifest(agent=AgentInfo(id="assistant"))
    return LoadedCore(
        root=tmp_path / "core",
        manifest_path=tmp_path / "core" / "agent.yaml",
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul="",
        bootstrap_slots=slots,
        bootstrap_pipeline=PhasePipeline(serial=slots),
        bootstrap_enabled=enabled,
        input_slots=[],
        output_slots=[],
        input_pipeline=PhasePipeline(),
        output_pipeline=PhasePipeline(),
        tool_slots=[],
        skills=[],
        schedules=[],
        mcp_servers=[],
    )


class _Host:
    def __init__(self, tmp_path: Path):
        self.slot_runtime = SlotRuntime()
        self.session_runtime = SessionRuntime(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
        self.session_runtime.ensure_session("session_1", core_id="assistant", core_revision="rev_1")
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}


def _request(tmp_path: Path, host: _Host, core: LoadedCore, *, workspace: str | None = None) -> BootstrapSlotRequest:
    return BootstrapSlotRequest(
        session_id="session_1",
        core=core,
        core_revision="rev_1",
        capability=CapabilityFacade(core),
        workspace=workspace,
        interaction_metadata={"channel": "tui"},
    )


@pytest.mark.asyncio
async def test_bootstrap_runtime_skips_when_disabled(tmp_path):
    host = _Host(tmp_path)
    runtime = BootstrapSlotRuntime(host)
    core = _core(
        tmp_path,
        slots=[_slot(tmp_path, "boot", "def process(ctx):\n    ctx.bootstrap.add('BOOT')\n")],
        enabled=False,
    )

    await runtime.ensure(_request(tmp_path, host, core))

    assert not host.session_runtime.bootstrap_context_exists("session_1")
    assert host.events == []


@pytest.mark.asyncio
async def test_bootstrap_runtime_reuses_existing_snapshot(tmp_path):
    host = _Host(tmp_path)
    host.session_runtime.write_bootstrap_context("session_1", "EXISTING")
    runtime = BootstrapSlotRuntime(host)
    core = _core(tmp_path, slots=[_slot(tmp_path, "boot", "def process(ctx):\n    raise RuntimeError('should not run')\n")])

    await runtime.ensure(_request(tmp_path, host, core))

    assert host.session_runtime.read_bootstrap_context("session_1") == "EXISTING"
    assert host.events == []


@pytest.mark.asyncio
async def test_bootstrap_runtime_persists_fragments_and_ignores_return_value(tmp_path):
    host = _Host(tmp_path)
    runtime = BootstrapSlotRuntime(host)
    slot = _slot(
        tmp_path,
        "boot",
        "def process(ctx):\n"
        "    ctx.bootstrap.add('A')\n"
        "    ctx.bootstrap.add(ctx.bootstrap.workspace)\n"
        "    return 'ignored'\n",
    )
    core = _core(tmp_path, slots=[slot])

    await runtime.ensure(_request(tmp_path, host, core, workspace=str(tmp_path / "workspace")))

    assert host.session_runtime.read_bootstrap_context("session_1") == f"A\n\n{tmp_path / 'workspace'}"
    assert [event[0] for event in host.events] == [
        "bootstrap.started",
        "bootstrap.module.started",
        "bootstrap.module.return_ignored",
        "bootstrap.module.completed",
        "bootstrap.completed",
    ]
    assert {event[1]["session_id"] for event in host.events} == {"session_1"}


@pytest.mark.asyncio
async def test_bootstrap_runtime_soft_failure_writes_empty_snapshot_and_continues(tmp_path):
    host = _Host(tmp_path)
    runtime = BootstrapSlotRuntime(host)
    core = _core(tmp_path, slots=[_slot(tmp_path, "boot", "def process(ctx):\n    raise RuntimeError('boom')\n")])

    await runtime.ensure(_request(tmp_path, host, core))

    assert host.session_runtime.bootstrap_context_exists("session_1")
    assert host.session_runtime.read_bootstrap_context("session_1") == ""
    assert [event[0] for event in host.events] == [
        "bootstrap.started",
        "bootstrap.module.started",
        "bootstrap.module.failed",
        "bootstrap.completed",
    ]


@pytest.mark.asyncio
async def test_bootstrap_runtime_hard_failure_raises_without_snapshot(tmp_path):
    host = _Host(tmp_path)
    runtime = BootstrapSlotRuntime(host)
    slot = _slot(
        tmp_path,
        "boot",
        "def process(ctx):\n    raise RuntimeError('boom')\n",
        failure_policy="hard",
    )
    core = _core(tmp_path, slots=[slot])

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.ensure(_request(tmp_path, host, core))

    assert not host.session_runtime.bootstrap_context_exists("session_1")
    assert [event[0] for event in host.events] == [
        "bootstrap.started",
        "bootstrap.module.started",
        "bootstrap.module.failed",
        "bootstrap.failed",
    ]
