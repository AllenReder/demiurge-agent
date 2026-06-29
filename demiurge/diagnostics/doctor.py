from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from demiurge.core import AgentFallbackConfig, CoreLoadError, CoreLoader, LoadedCore
from demiurge.storage import VersionStore


@dataclass(slots=True)
class DoctorFinding:
    severity: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DoctorReport:
    home: str
    source_agents_root: str
    core_id: str
    findings: list[DoctorFinding] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.findings)

    def counts(self) -> dict[str, int]:
        counts = {"ok": 0, "warning": 0, "error": 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": self.home,
            "source_agents_root": self.source_agents_root,
            "core_id": self.core_id,
            "counts": self.counts(),
            "ok": not self.has_errors,
            "findings": [item.to_dict() for item in self.findings],
        }


class DoctorRuntime:
    def __init__(
        self,
        *,
        home: Path,
        source_agents_root: Path,
        core_id: str = "assistant",
        core_loader: CoreLoader | None = None,
    ):
        self.home = home.resolve()
        self.source_agents_root = source_agents_root.resolve()
        self.core_id = core_id
        self.version_store = VersionStore(self.home)
        self.core_loader = core_loader or CoreLoader()

    def run(self) -> DoctorReport:
        report = DoctorReport(
            home=str(self.home),
            source_agents_root=str(self.source_agents_root),
            core_id=self.core_id,
        )
        self._check_source_root(report)
        self._check_fallback(report)
        for core_id in dict.fromkeys([self.core_id, "assistant", "evolver"]):
            self._check_core(report, core_id)
        if not report.findings:
            report.findings.append(
                DoctorFinding(
                    severity="ok",
                    code="doctor.ok",
                    message="runtime and source agent templates look consistent",
                )
            )
        return report

    def _check_source_root(self, report: DoctorReport) -> None:
        if not self.source_agents_root.exists():
            report.findings.append(
                DoctorFinding(
                    severity="error",
                    code="source_agents_root.missing",
                    message=f"source agents root does not exist: {self.source_agents_root}",
                    remediation="Pass --agents-root or set DEMIURGE_AGENTS_ROOT.",
                )
            )
        elif not self.source_agents_root.is_dir():
            report.findings.append(
                DoctorFinding(
                    severity="error",
                    code="source_agents_root.not_directory",
                    message=f"source agents root is not a directory: {self.source_agents_root}",
                )
            )

    def _check_fallback(self, report: DoctorReport) -> None:
        source_path = self.source_agents_root / "agent.yaml"
        runtime_path = self.version_store.fallback_config_path
        source = self._load_fallback(source_path, report, "source")
        runtime = self._load_fallback(runtime_path, report, "runtime")
        if source is None or runtime is None:
            return
        source_keys = set(self._yaml_keys(source_path))
        runtime_keys = set(self._yaml_keys(runtime_path))
        missing = sorted(source_keys - runtime_keys)
        if missing:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="global_config.missing_fields",
                    message="runtime global agent config is missing fields from the source template",
                    details={"missing": missing, "runtime_path": str(runtime_path)},
                    remediation="Run `uv run demiurge init --refresh global` after reviewing your local config.",
                )
            )
        self._check_model_envs(report, "runtime global fallback", runtime.model)

    def _load_fallback(
        self,
        path: Path,
        report: DoctorReport,
        label: str,
    ) -> AgentFallbackConfig | None:
        if not path.exists():
            report.findings.append(
                DoctorFinding(
                    severity="error" if label == "runtime" else "warning",
                    code=f"{label}.fallback.missing",
                    message=f"{label} global fallback config is missing: {path}",
                    remediation="Run `uv run demiurge init --refresh global`." if label == "runtime" else None,
                )
            )
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return AgentFallbackConfig.model_validate(raw)
        except (ValidationError, yaml.YAMLError) as exc:
            report.findings.append(
                DoctorFinding(
                    severity="error",
                    code=f"{label}.fallback.invalid",
                    message=f"{label} global fallback config is invalid: {path}",
                    details={"error": str(exc)},
                    remediation="Global `agents/agent.yaml` can only contain global fields such as model, ui, and approval.",
                )
            )
            return None

    def _check_core(self, report: DoctorReport, core_id: str) -> None:
        source_path = self.source_agents_root / core_id
        runtime_path = self.version_store.active_core_path(core_id)
        source = self._load_core(report, core_id, source_path, "source")
        runtime = self._load_core(report, core_id, runtime_path, "runtime")
        if source is None or runtime is None:
            return
        self._compare_allowed_tools(report, core_id, source, runtime)
        self._compare_manifest_slots(report, core_id, source, runtime)
        self._compare_slot_ids(report, core_id, "input", source.input_slots, runtime.input_slots)
        self._compare_slot_ids(report, core_id, "output", source.output_slots, runtime.output_slots)
        self._compare_slot_ids(report, core_id, "tools", source.tool_slots, runtime.tool_slots)
        self._compare_skills(report, core_id, source, runtime)
        self._check_ui(report, core_id, runtime)
        self._check_model_envs(report, f"runtime {core_id}", runtime.manifest.model)

    def _load_core(
        self,
        report: DoctorReport,
        core_id: str,
        path: Path,
        label: str,
    ) -> LoadedCore | None:
        if not path.exists():
            report.findings.append(
                DoctorFinding(
                    severity="error" if label == "runtime" else "warning",
                    code=f"{label}.core.missing",
                    message=f"{label} core `{core_id}` is missing: {path}",
                    remediation=f"Run `uv run demiurge init --refresh {core_id}`." if label == "runtime" else None,
                )
            )
            return None
        try:
            return self.core_loader.load(path)
        except (CoreLoadError, ValueError, OSError) as exc:
            report.findings.append(
                DoctorFinding(
                    severity="error",
                    code=f"{label}.core.invalid",
                    message=f"{label} core `{core_id}` failed to load: {path}",
                    details={"error": str(exc)},
                    remediation=f"Run `uv run demiurge init --refresh {core_id}`." if label == "runtime" else None,
                )
            )
            return None

    def _compare_allowed_tools(
        self,
        report: DoctorReport,
        core_id: str,
        source: LoadedCore,
        runtime: LoadedCore,
    ) -> None:
        source_tools = set(source.builtin_tool_names)
        runtime_tools = set(runtime.builtin_tool_names)
        missing = sorted(source_tools - runtime_tools)
        extra = sorted(runtime_tools - source_tools)
        if missing:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="core.tools.missing",
                    message=f"runtime `{core_id}` is missing tools from the source template",
                    details={"core_id": core_id, "missing": missing},
                    remediation=f"Run `uv run demiurge init --refresh {core_id}` or add the tools after review.",
                )
            )
        if extra:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="core.tools.extra",
                    message=f"runtime `{core_id}` has tools not present in the source template",
                    details={"core_id": core_id, "extra": extra},
                    remediation="Review whether these local runtime changes are intentional.",
                )
            )

    def _compare_manifest_slots(
        self,
        report: DoctorReport,
        core_id: str,
        source: LoadedCore,
        runtime: LoadedCore,
    ) -> None:
        missing = sorted(set(source.manifest.slots) - set(runtime.manifest.slots))
        if missing:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="core.slots.missing_keys",
                    message=f"runtime `{core_id}` is missing slot declarations from the source template",
                    details={"core_id": core_id, "missing": missing},
                    remediation=f"Run `uv run demiurge init --refresh {core_id}` or update agent.yaml manually.",
                )
            )

    def _compare_slot_ids(
        self,
        report: DoctorReport,
        core_id: str,
        kind: str,
        source_slots: list[Any],
        runtime_slots: list[Any],
    ) -> None:
        source_ids = {slot.slot_id for slot in source_slots}
        runtime_ids = {slot.slot_id for slot in runtime_slots}
        missing = sorted(source_ids - runtime_ids)
        if missing:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code=f"core.{kind}.missing_slots",
                    message=f"runtime `{core_id}` is missing {kind} slots from the source template",
                    details={"core_id": core_id, "missing": missing},
                    remediation=f"Run `uv run demiurge init --refresh {core_id}` or copy the missing slots manually.",
                )
            )

    def _compare_skills(self, report: DoctorReport, core_id: str, source: LoadedCore, runtime: LoadedCore) -> None:
        source_skills = {skill.skill_id: skill for skill in source.skills}
        runtime_skills = {skill.skill_id: skill for skill in runtime.skills}
        missing = sorted(set(source_skills) - set(runtime_skills))
        if missing:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="core.skills.missing",
                    message=f"runtime `{core_id}` is missing skills from the source template",
                    details={"core_id": core_id, "missing": missing},
                    remediation=f"Run `uv run demiurge init --refresh {core_id}` or copy the missing skills manually.",
                )
            )
        changed: list[str] = []
        for skill_id, source_skill in source_skills.items():
            runtime_skill = runtime_skills.get(skill_id)
            if not runtime_skill:
                continue
            if source_skill.category != runtime_skill.category or source_skill.linked_files != runtime_skill.linked_files:
                changed.append(skill_id)
        if changed:
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="core.skills.metadata_drift",
                    message=f"runtime `{core_id}` has skill metadata drift from the source template",
                    details={"core_id": core_id, "skills": sorted(changed)},
                    remediation="Review local skill changes before refreshing.",
                )
            )

    def _check_ui(self, report: DoctorReport, core_id: str, runtime: LoadedCore) -> None:
        value = runtime.manifest.ui.tool_display
        if value and value not in {"quiet", "summary", "full"}:
            report.findings.append(
                DoctorFinding(
                    severity="error",
                    code="core.ui.invalid_tool_display",
                    message=f"runtime `{core_id}` has invalid ui.tool_display",
                    details={"value": value},
                    remediation="Set ui.tool_display to quiet, summary, or full.",
                )
            )

    def _check_model_envs(self, report: DoctorReport, label: str, model: Any) -> None:
        for attr in ("model_name_env", "base_url_env", "api_key_env"):
            env_name = getattr(model, attr, None)
            direct_attr = attr.removesuffix("_env")
            if not env_name or getattr(model, direct_attr, None):
                continue
            if os.environ.get(env_name):
                continue
            report.findings.append(
                DoctorFinding(
                    severity="warning",
                    code="provider.env_missing",
                    message=f"{label} references unset environment variable `{env_name}`",
                    details={"field": attr, "env": env_name},
                    remediation=f"Export {env_name}=... or configure a direct value if appropriate.",
                )
            )

    def _yaml_keys(self, path: Path) -> list[str]:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return []
        if not isinstance(raw, dict):
            return []
        return sorted(str(key) for key in raw)
