from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from demiurge.core import CoreLoadError, CoreLoader
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker
from demiurge.providers import FakeProvider
from demiurge.storage import VersionStore
from demiurge.tools.runtime import ToolRuntime


@dataclass(slots=True)
class GatePhase:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class GateResult:
    passed: bool
    phases: list[GatePhase] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "phases": [asdict(phase) for phase in self.phases],
        }


class GateRunner:
    def __init__(self, *, project_root: Path):
        self.project_root = project_root
        self.loader = CoreLoader()

    async def run(self, candidate_path: Path) -> GateResult:
        phases: list[GatePhase] = []
        core = None
        try:
            core = self.loader.load(candidate_path)
            phases.append(GatePhase("manifest", True, f"loaded {core.core_id}@{core.version}"))
        except CoreLoadError as exc:
            phases.append(GatePhase("manifest", False, str(exc)))
            return GateResult(False, phases)

        phases.append(self._dependency_gate(candidate_path, core.raw_manifest))
        phases.append(self._capability_gate(core.raw_manifest))
        if all(phase.passed for phase in phases):
            phases.extend(self._run_candidate_tests(candidate_path, core.manifest.tests.commands))
        if all(phase.passed for phase in phases):
            phases.append(await self._fake_smoke(candidate_path))
        return GateResult(all(phase.passed for phase in phases), phases)

    def _dependency_gate(self, candidate_path: Path, raw_manifest: dict[str, Any]) -> GatePhase:
        forbidden = [
            path.relative_to(candidate_path).as_posix()
            for path in candidate_path.rglob("*")
            if path.name in {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.in"}
        ]
        if forbidden:
            return GatePhase("dependency", False, f"candidate declares dependencies: {forbidden}")
        dependency_policy = raw_manifest.get("dependencies", {}) or {}
        if dependency_policy.get("allow_additional_dependencies", False):
            return GatePhase("dependency", False, "additional dependencies require manual review")
        return GatePhase("dependency", True, "host_shared dependencies only")

    def _capability_gate(self, raw_manifest: dict[str, Any]) -> GatePhase:
        allowed_prefixes = (
            "llm.call",
            "agents.run",
            "agents.run:",
            "agents.spawn",
            "agents.spawn:",
            "state.read",
            "state.read:",
            "state.propose",
            "state.write",
            "state.write:",
            "skill.activate",
            "skill.activate:",
            "tool.call",
            "tool.call:",
            "fs.read",
            "fs.write",
            "fs.delete",
            "terminal.exec",
        )
        caps = raw_manifest.get("capabilities", {}) or {}
        serialized = str(caps)
        unknown = []
        for token in serialized.replace("{", " ").replace("}", " ").replace("'", " ").split():
            if "." in token and ":" in token and not token.startswith(allowed_prefixes):
                unknown.append(token)
        if unknown:
            return GatePhase("capability", False, f"unknown capability token(s): {unknown[:5]}")
        return GatePhase("capability", True, "capability declarations accepted")

    def _run_candidate_tests(self, candidate_path: Path, commands: list[str]) -> list[GatePhase]:
        if not commands:
            return [GatePhase("candidate_tests", True, "no candidate test commands declared")]
        phases: list[GatePhase] = []
        for command in commands:
            args = shlex.split(command)
            if args[:3] != ["uv", "run", "pytest"]:
                phases.append(GatePhase("candidate_tests", False, f"unsupported test command: {command}"))
                continue
            env = os.environ.copy()
            env["DEMIURGE_CANDIDATE"] = str(candidate_path)
            completed = subprocess.run(
                args,
                cwd=self.project_root,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            detail = (completed.stdout + "\n" + completed.stderr).strip()[-4000:]
            phases.append(GatePhase("candidate_tests", completed.returncode == 0, detail))
        return phases

    async def _fake_smoke(self, candidate_path: Path) -> GatePhase:
        try:
            core = self.loader.load(candidate_path)
            script = core.manifest.tests.smoke.fake_llm_script
            script_path = candidate_path / script if script else None
            with tempfile.TemporaryDirectory(prefix="demiurge-smoke-") as tmp:
                home = Path(tmp)
                version_store = VersionStore(home)
                control_plane = RuntimeControlPlane(RuntimeStore.default(home))
                session_runtime = SessionRuntime(control_plane=control_plane)
                task_worker = RuntimeTaskWorker(control_plane=control_plane)
                tool_runtime = ToolRuntime(version_store, session_runtime=session_runtime, task_worker=task_worker)
                runner = SessionTurnStepRunner(
                    home=home,
                    version_store=version_store,
                    core_loader=self.loader,
                    provider=FakeProvider(script_path),
                    tool_runtime=tool_runtime,
                    core_id=core.core_id,
                    initial_core_path=candidate_path,
                    session_runtime=session_runtime,
                    task_worker=task_worker,
                )
                result = await runner.run_turn("smoke tools_list", core_path=candidate_path)
            if not result.deliveries:
                return GatePhase("fake_llm_smoke", False, "no assistant message produced")
            return GatePhase("fake_llm_smoke", True, "host loop completed with fake provider")
        except Exception as exc:
            return GatePhase("fake_llm_smoke", False, str(exc))


def run_gate_sync(runner: GateRunner, candidate_path: Path) -> GateResult:
    return asyncio.run(runner.run(candidate_path))
