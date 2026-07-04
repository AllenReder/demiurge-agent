from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

if os.name != "nt":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]
    import msvcrt

import yaml

from demiurge.util import ensure_dir, utc_id, write_json


LIVE_REF = "refs/demiurge/live"
PREVIOUS_REF = "refs/demiurge/previous"
RUN_REF_PREFIX = "refs/demiurge/runs"
PROTECTED_DEPENDENCY_FILES = {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.in"}
CORE_EXCLUDE_PATTERNS = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
)
GENERATED_ARTIFACT_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
GENERATED_ARTIFACT_SUFFIXES = {".pyc", ".pyo"}
GENERATED_RUNTIME_DIR_NAMES = {".evolve", "runtime", "logs", "runs", "history", "registry"}


class CoreRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CoreConsistencyIssue:
    code: str
    message: str
    remediation: str
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class CoreConsistencyReport:
    ok: bool
    issues: list[CoreConsistencyIssue] = field(default_factory=list)
    live_revision: str | None = None
    previous_revision: str | None = None
    checkout_head: str | None = None


@dataclass(frozen=True, slots=True)
class CorePointer:
    core_id: str
    active_revision: str
    previous_revision: str | None = None
    reason: str = "live"


@dataclass(frozen=True, slots=True)
class CommitResult:
    revision: str
    previous_revision: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class DiffSummary:
    changed_paths: list[str] = field(default_factory=list)
    name_status: str = ""
    stat: str = ""


@dataclass(frozen=True, slots=True)
class LocalEditDescription:
    changed_paths: list[str] = field(default_factory=list)
    changed_scopes: list[str] = field(default_factory=list)
    detected_changes: list[str] = field(default_factory=list)
    summary: str = "no local agent edits"


@dataclass(frozen=True, slots=True)
class LocalEditSaveResult:
    saved: bool
    commit: CommitResult | None
    description: LocalEditDescription
    gates: Any | None = None


class CoreRepository:
    """Git-backed repository for the runtime Agent Core tree.

    The external interface is the runtime agents tree. Callers do not need to
    know whether a mutation is committed from the live worktree or from an
    isolated evolve worktree.
    """

    def __init__(self, home: Path):
        self.home = home.expanduser().resolve()
        self.agents_root = self.home / "agents"
        self.git_dir = self.home / ".core.git"
        self.core_ignore_path = self.home / ".core-ignore"
        self.evolve_root = self.home / ".evolve" / "runs"
        self.lock_path = self.home / ".core.lock"

    @property
    def fallback_config_path(self) -> Path:
        return self.agents_root / "agent.yaml"

    def active_agents_root(self) -> Path:
        self.require_initialized()
        return self.agents_root

    def active_core_path(self, core_id: str) -> Path:
        return self.agents_root / core_id

    def require_initialized(self) -> None:
        if not self.git_dir.exists():
            raise CoreRepositoryError(
                f"core repository is not initialized: {self.git_dir}; "
                "delete the old runtime home or run `demiurge init` for a fresh core repository"
            )
        self.ensure_private_excludes()

    def initialize_from_source(self, source_agents: Path, *, reason: str = "init", force: bool = False) -> CorePointer:
        source_agents = source_agents.expanduser().resolve()
        if not source_agents.exists() or not source_agents.is_dir():
            raise FileNotFoundError(f"source agents root not found: {source_agents}")
        with self.locked():
            if self.git_dir.exists():
                revision = self.live_revision()
                return CorePointer(core_id="agents", active_revision=revision, previous_revision=self.previous_revision(), reason=reason)
            if self.agents_root.exists() and any(self.agents_root.iterdir()):
                if not force:
                    raise CoreRepositoryError(
                        f"runtime agents tree already exists without {self.git_dir}: {self.agents_root}; "
                        "this version does not migrate legacy runtime homes"
                    )
                shutil.rmtree(self.agents_root)
            ensure_dir(self.home)
            shutil.copytree(source_agents, self.agents_root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            ensure_dir(self.git_dir.parent)
            self._run(["init", "--bare", str(self.git_dir)], cwd=None, prefix=["git"])
            self._run_git(["config", "user.name", "Demiurge Host"])
            self._run_git(["config", "user.email", "demiurge@localhost"])
            self.ensure_private_excludes()
            self._run_git(["add", "-A"], work_tree=self.agents_root)
            self._run_git(["commit", "-m", self._commit_message("core init", reason)], work_tree=self.agents_root)
            revision = self._run_git(["rev-parse", "HEAD"], work_tree=self.agents_root).stdout.strip()
            self._run_git(["update-ref", LIVE_REF, revision])
            self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)
            return CorePointer(core_id="agents", active_revision=revision, previous_revision=None, reason=reason)

    def ensure_initialized(self, source_agents: Path) -> None:
        if self.git_dir.exists():
            self.ensure_private_excludes()
            return
        self.initialize_from_source(source_agents, reason="auto init", force=False)

    def ensure_private_excludes(self) -> None:
        ensure_dir(self.home)
        lines = [
            "# Demiurge host-owned ignores for the runtime core repository.",
            "# This file is not part of the authored agents tree.",
            *CORE_EXCLUDE_PATTERNS,
            "",
        ]
        content = "\n".join(lines)
        if not self.core_ignore_path.exists() or self.core_ignore_path.read_text(encoding="utf-8") != content:
            self.core_ignore_path.write_text(content, encoding="utf-8")
        for _ in range(5):
            configured = self._run_git(["config", "--get", "core.excludesFile"], check=False).stdout.strip()
            if configured == str(self.core_ignore_path):
                return
            result = self._run_git(["config", "core.excludesFile", str(self.core_ignore_path)], check=False)
            if result.returncode == 0:
                return
            time.sleep(0.05)
        self._run_git(["config", "core.excludesFile", str(self.core_ignore_path)])

    def refresh_from_source(self, source_agents: Path, *, reason: str = "refresh") -> CommitResult:
        self.require_initialized()
        source_agents = source_agents.expanduser().resolve()
        if not source_agents.exists() or not source_agents.is_dir():
            raise FileNotFoundError(f"source agents root not found: {source_agents}")
        with self.live_transaction(reason=reason):
            for child in list(self.agents_root.iterdir()):
                if child.name == ".git":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in source_agents.iterdir():
                target = self.agents_root / child.name
                if child.is_dir():
                    shutil.copytree(child, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
                else:
                    shutil.copy2(child, target)
            return self.commit_live(reason=reason, summary="refresh runtime agents from source")

    def ensure_core_from_source(self, core_id: str, source_core_path: Path, *, reason: str = "auto init") -> CorePointer:
        self.require_initialized()
        target = self.active_core_path(core_id)
        if target.exists():
            return self.active_pointer(core_id)
        source_core_path = source_core_path.expanduser().resolve()
        if not (source_core_path / "agent.yaml").exists():
            raise FileNotFoundError(f"source agent core missing agent.yaml: {source_core_path}")
        with self.live_transaction(reason=reason):
            shutil.copytree(source_core_path, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            self.commit_live(reason=reason, summary=f"add runtime core {core_id}")
        return self.active_pointer(core_id, reason=reason)

    def active_pointer(self, core_id: str, *, reason: str = "live") -> CorePointer:
        return CorePointer(
            core_id=core_id,
            active_revision=self.live_revision(),
            previous_revision=self.previous_revision(),
            reason=reason,
        )

    def live_revision(self) -> str:
        self.require_initialized()
        return self._run_git(["rev-parse", LIVE_REF]).stdout.strip()

    def previous_revision(self) -> str | None:
        self.require_initialized()
        result = self._run_git(["rev-parse", "--verify", PREVIOUS_REF], check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def list_revisions(self, limit: int = 30) -> list[str]:
        self.require_initialized()
        result = self._run_git(["log", f"--max-count={limit}", "--format=%H", LIVE_REF], check=False)
        if result.returncode != 0:
            return [self.live_revision()]
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def status(self) -> dict[str, Any]:
        self.require_initialized()
        consistency = self.check_consistency()
        description = self.describe_local_edits() if self.agents_root.exists() else LocalEditDescription(summary="live agents checkout is missing")
        return {
            "agents_root": str(self.agents_root),
            "git_dir": str(self.git_dir),
            "live": consistency.live_revision,
            "previous": consistency.previous_revision,
            "dirty": bool(description.changed_paths),
            "changed_paths": description.changed_paths,
            "changed_scopes": description.changed_scopes,
            "detected_changes": description.detected_changes,
            "summary": description.summary,
            "consistency": _consistency_report_to_dict(consistency),
        }

    def check_consistency(self) -> CoreConsistencyReport:
        if not self.git_dir.exists():
            return CoreConsistencyReport(
                ok=False,
                issues=[
                    CoreConsistencyIssue(
                        code="core.repository.missing",
                        message=f"core repository is not initialized: {self.git_dir}",
                        remediation="Run `demiurge init` to initialize the runtime core repository.",
                    )
                ],
            )
        self.ensure_private_excludes()
        issues: list[CoreConsistencyIssue] = []
        live_revision = self._resolve_ref(LIVE_REF)
        if live_revision is None:
            issues.append(
                CoreConsistencyIssue(
                    code="core.live_ref.missing",
                    message=f"{LIVE_REF} is missing or does not resolve to a commit",
                    remediation="Inspect `demiurge core status`; restore from backup or re-run `demiurge init --refresh all` after review.",
                )
            )
        checkout_head = self._resolve_checkout_head() if self.agents_root.exists() else None
        if live_revision and not self.agents_root.exists():
            issues.append(
                CoreConsistencyIssue(
                    code="core.checkout_missing",
                    message=f"live checkout is missing: {self.agents_root}",
                    remediation="Run `demiurge init --refresh all` after reviewing the runtime home, or restore the agents checkout from backup.",
                )
            )
        elif live_revision and checkout_head is None:
            issues.append(
                CoreConsistencyIssue(
                    code="core.checkout_head_missing",
                    message=f"live checkout HEAD could not be resolved: {self.agents_root}",
                    remediation="Inspect `demiurge core status`; restore or reset the runtime agents checkout before mutating the core.",
                )
            )
        if live_revision and checkout_head and checkout_head != live_revision:
            issues.append(
                CoreConsistencyIssue(
                    code="core.checkout_head_mismatch",
                    message=f"live checkout HEAD {checkout_head} does not match {LIVE_REF} {live_revision}",
                    remediation="Inspect `demiurge core status`; save or discard local edits, then reset the runtime agents tree to the live ref.",
                )
            )
        previous_revision = self._resolve_ref(PREVIOUS_REF, missing_ok=True)
        if previous_revision == "":
            issues.append(
                CoreConsistencyIssue(
                    code="core.previous_ref.invalid",
                    message=f"{PREVIOUS_REF} exists but does not resolve to a commit",
                    remediation="Inspect `demiurge core status`; delete or restore the previous ref before rollback.",
                )
            )
            previous_revision = None
        if live_revision and previous_revision and previous_revision == live_revision:
            issues.append(
                CoreConsistencyIssue(
                    code="core.previous_ref_matches_live",
                    message=f"{PREVIOUS_REF} points at the same revision as {LIVE_REF}",
                    remediation="Inspect `demiurge core status`; rollback history may need manual repair before using rollback.",
                )
            )
        return CoreConsistencyReport(
            ok=not issues,
            issues=issues,
            live_revision=live_revision,
            previous_revision=previous_revision,
            checkout_head=checkout_head,
        )

    def live_changed_paths(self) -> list[str]:
        self.require_initialized()
        result = self._run_git(["status", "--porcelain"], work_tree=self.agents_root)
        return _paths_from_porcelain(result.stdout)

    def clean_ignored_artifacts(self) -> None:
        self.require_initialized()
        self._run_git(["clean", "-fdX"], work_tree=self.agents_root)

    def require_live_clean(self) -> None:
        self._require_consistent_for_mutation()
        changed = self.live_changed_paths()
        if changed:
            raise CoreRepositoryError(f"live agents tree has uncommitted changes: {changed}")

    def describe_local_edits(self) -> LocalEditDescription:
        changed_paths = self.live_changed_paths()
        if not changed_paths:
            return LocalEditDescription()
        scopes = _changed_scopes(changed_paths)
        detected = self._detected_local_changes(changed_paths)
        summary = _local_edit_summary(changed_paths, scopes, detected)
        return LocalEditDescription(
            changed_paths=changed_paths,
            changed_scopes=scopes,
            detected_changes=detected,
            summary=summary,
        )

    def local_diff(self) -> str:
        self.require_initialized()
        diff = self._run_git(["diff", LIVE_REF], work_tree=self.agents_root).stdout
        untracked = [
            path
            for path in self.live_changed_paths()
            if self._run_git(["ls-files", "--error-unmatch", path], work_tree=self.agents_root, check=False).returncode != 0
        ]
        if not untracked:
            return diff
        lines = [diff.rstrip(), "", "Untracked paths:", *[f"- {path}" for path in untracked]]
        return "\n".join(line for line in lines if line != "") + "\n"

    def prepare_live_for_edit(
        self,
        *,
        validate: Callable[[Path, list[str]], Any] | None = None,
        gates: Any | None = None,
    ) -> LocalEditSaveResult:
        return self.save_local_edits(validate=validate, gates=gates)

    async def prepare_live_for_edit_async(
        self,
        *,
        validate: Callable[[Path, list[str]], Awaitable[Any]] | None = None,
        gates: Any | None = None,
    ) -> LocalEditSaveResult:
        return await self.save_local_edits_async(validate=validate, gates=gates)

    def prepare_live_for_switch(self) -> None:
        self._require_consistent_for_mutation()
        changed = self.live_changed_paths()
        if changed:
            raise CoreRepositoryError(
                "local agent edits must be saved or discarded before switching core revisions: "
                f"{changed}; run `demiurge core save` or `demiurge core discard --yes`"
            )

    def save_local_edits(
        self,
        *,
        validate: Callable[[Path, list[str]], Any] | None = None,
        gates: Any | None = None,
    ) -> LocalEditSaveResult:
        with self.locked():
            self._require_consistent_for_mutation()
            self.clean_ignored_artifacts()
            description = self.describe_local_edits()
            if not description.changed_paths:
                return LocalEditSaveResult(saved=False, commit=None, description=description, gates=gates)
            resolved_gates = gates if gates is not None else validate(self.agents_root, description.changed_paths) if validate else None
            self._require_gates_passed(resolved_gates)
            commit = self._commit_local_edits(description, gates=resolved_gates)
            return LocalEditSaveResult(saved=True, commit=commit, description=description, gates=resolved_gates)

    async def save_local_edits_async(
        self,
        *,
        validate: Callable[[Path, list[str]], Awaitable[Any]] | None = None,
        gates: Any | None = None,
    ) -> LocalEditSaveResult:
        with self.locked():
            self._require_consistent_for_mutation()
            self.clean_ignored_artifacts()
            description = self.describe_local_edits()
            if not description.changed_paths:
                return LocalEditSaveResult(saved=False, commit=None, description=description, gates=gates)
            resolved_gates = gates
            if resolved_gates is None and validate is not None:
                resolved_gates = await validate(self.agents_root, description.changed_paths)
            self._require_gates_passed(resolved_gates)
            commit = self._commit_local_edits(description, gates=resolved_gates)
            return LocalEditSaveResult(saved=True, commit=commit, description=description, gates=resolved_gates)

    def discard_local_edits(self) -> LocalEditDescription:
        with self.locked():
            description = self.describe_local_edits()
            if not description.changed_paths:
                return description
            self.reset_live()
            self._run_git(["clean", "-fd"], work_tree=self.agents_root)
            self.clean_ignored_artifacts()
            return description

    def begin_change_set(self, *, kind: str, reason: str, run_id: str | None = None) -> "CoreChangeSet":
        self.require_initialized()
        self._require_consistent_for_mutation()
        run_id = run_id or utc_id(f"{kind}_")
        run_root = self.evolve_root / run_id
        agents_root = run_root / "agents"
        if agents_root.exists():
            raise FileExistsError(f"core change set already exists: {agents_root}")
        ensure_dir(run_root)
        self._run_git(["worktree", "add", "--detach", str(agents_root), LIVE_REF])
        write_json(
            run_root / "request.json",
            {
                "run_id": run_id,
                "kind": kind,
                "reason": reason,
                "base_revision": self.live_revision(),
            },
        )
        return CoreChangeSet(repository=self, run_id=run_id, kind=kind, run_root=run_root, agents_root=agents_root)

    def change_set(self, run_id: str) -> "CoreChangeSet":
        run_root = self.evolve_root / run_id
        agents_root = run_root / "agents"
        if not agents_root.exists():
            raise FileNotFoundError(f"evolve run not found: {run_id}")
        return CoreChangeSet(repository=self, run_id=run_id, kind="evolve", run_root=run_root, agents_root=agents_root)

    def run_ref(self, run_id: str) -> str:
        return f"{RUN_REF_PREFIX}/{run_id}"

    def promote_run(self, run_id: str, *, reason: str) -> CommitResult:
        self.require_initialized()
        with self.locked():
            self.prepare_live_for_switch()
            ref = self.run_ref(run_id)
            proposal = self._run_git(["rev-parse", "--verify", ref]).stdout.strip()
            previous = self.live_revision()
            base_revision = self._change_set_base_revision(run_id)
            if base_revision != previous:
                raise CoreRepositoryError(
                    f"stale core proposal: run_id={run_id} base={base_revision} current={previous}; "
                    "review or rebase the evolve run before promoting"
                )
            self._update_core_refs(previous=previous, new_live=proposal, expected_live=previous)
            self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)
            return CommitResult(revision=proposal, previous_revision=previous, summary=f"promoted {run_id} to live")

    def commit_live(self, *, reason: str, summary: str) -> CommitResult:
        self.require_initialized()
        self._require_consistent_for_mutation()
        previous = self.live_revision()
        self._run_git(["add", "-A"], work_tree=self.agents_root)
        if not self.live_changed_paths():
            return CommitResult(revision=previous, previous_revision=self.previous_revision(), summary="no live changes to commit")
        self._run_git(["commit", "-m", self._commit_message(summary, reason)], work_tree=self.agents_root)
        revision = self._run_git(["rev-parse", "HEAD"], work_tree=self.agents_root).stdout.strip()
        self._update_core_refs(previous=previous, new_live=revision, expected_live=previous)
        return CommitResult(revision=revision, previous_revision=previous, summary=summary)

    def rollback(self, target: str = "previous", *, reason: str = "rollback") -> CommitResult:
        self.require_initialized()
        with self.locked():
            self._require_consistent_for_mutation()
            self.prepare_live_for_switch()
            current = self.live_revision()
            if target == "previous":
                target_revision = self.previous_revision()
                if not target_revision:
                    raise CoreRepositoryError("no previous core revision recorded")
            else:
                target_revision = self._run_git(["rev-parse", "--verify", target]).stdout.strip()
            tree = self._run_git(["rev-parse", f"{target_revision}^{{tree}}"]).stdout.strip()
            message = self._commit_message(f"rollback core tree to {target_revision[:12]}", reason)
            commit = self._run_git(["commit-tree", tree, "-p", current, "-m", message]).stdout.strip()
            self._update_core_refs(previous=current, new_live=commit, expected_live=current)
            self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)
            return CommitResult(revision=commit, previous_revision=current, summary=f"rolled back to {target_revision}")

    @contextmanager
    def locked(self) -> Iterator[None]:
        ensure_dir(self.home)
        with self.lock_path.open("a+b") as lock_file:
            if fcntl is not None:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise CoreRepositoryError(f"core repository is locked: {self.lock_path}") from exc
            else:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise CoreRepositoryError(f"core repository is locked: {self.lock_path}") from exc
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                else:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    @contextmanager
    def live_transaction(self, *, reason: str) -> Iterator[Path]:
        with self.locked():
            self.require_live_clean()
            try:
                yield self.agents_root
            except BaseException as transaction_exc:
                try:
                    self._cleanup_failed_live_transaction()
                except BaseException as cleanup_exc:
                    raise CoreRepositoryError(
                        f"live transaction failed ({transaction_exc}) and cleanup also failed: {cleanup_exc}"
                    ) from transaction_exc
                raise

    def reset_live(self) -> None:
        self.require_initialized()
        self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)

    def _commit_local_edits(self, description: LocalEditDescription, *, gates: Any | None) -> CommitResult:
        self._require_consistent_for_mutation()
        previous = self.live_revision()
        self._run_git(["add", "-A"], work_tree=self.agents_root)
        if not self.live_changed_paths():
            return CommitResult(revision=previous, previous_revision=self.previous_revision(), summary="no live changes to commit")
        self._run_git(
            ["commit", "-m", self._local_edit_commit_message(description, gates=gates)],
            work_tree=self.agents_root,
        )
        revision = self._run_git(["rev-parse", "HEAD"], work_tree=self.agents_root).stdout.strip()
        self._update_core_refs(previous=previous, new_live=revision, expected_live=previous)
        return CommitResult(revision=revision, previous_revision=previous, summary=description.summary)

    def _cleanup_failed_live_transaction(self) -> None:
        self.reset_live()
        self._run_git(["clean", "-fd"], work_tree=self.agents_root)
        self.clean_ignored_artifacts()

    def _require_consistent_for_mutation(self) -> None:
        report = self.check_consistency()
        blocking = [issue for issue in report.issues if issue.severity == "error"]
        if blocking:
            detail = "; ".join(f"{issue.code}: {issue.message}" for issue in blocking)
            raise CoreRepositoryError(f"core repository consistency check failed: {detail}")

    def _update_core_refs(self, *, previous: str, new_live: str, expected_live: str) -> None:
        commands = "\n".join(
            [
                "start",
                f"update {PREVIOUS_REF} {previous}",
                f"update {LIVE_REF} {new_live} {expected_live}",
                "prepare",
                "commit",
                "",
            ]
        )
        self._run_git(["update-ref", "--stdin"], input=commands)

    def _change_set_base_revision(self, run_id: str) -> str:
        run_root = self.evolve_root / run_id
        request_path = run_root / "request.json"
        if not request_path.exists():
            raise CoreRepositoryError(f"core change set is missing request metadata: {request_path}")
        try:
            raw = json.loads(request_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CoreRepositoryError(f"core change set request metadata is invalid: {request_path}: {exc}") from exc
        base_revision = raw.get("base_revision")
        if not isinstance(base_revision, str) or not base_revision:
            raise CoreRepositoryError(f"core change set request metadata missing base_revision: {request_path}")
        resolved = self._run_git(["rev-parse", "--verify", base_revision], check=False)
        if resolved.returncode != 0:
            raise CoreRepositoryError(f"core change set base_revision does not resolve: run_id={run_id} base={base_revision}")
        return resolved.stdout.strip()

    def _resolve_ref(self, ref: str, *, missing_ok: bool = False) -> str | None:
        result = self._run_git(["rev-parse", "--verify", ref], check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        if missing_ok and (self.git_dir / ref).exists():
            return ""
        return None if missing_ok or "Needed a single revision" in result.stderr else None

    def _resolve_checkout_head(self) -> str | None:
        result = self._run_git(["rev-parse", "HEAD"], work_tree=self.agents_root, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _local_edit_commit_message(self, description: LocalEditDescription, *, gates: Any | None) -> str:
        lines = [
            description.summary,
            "",
            "Source: external edit of runtime agents tree",
            "Changed scopes:",
            *[f"- {scope}" for scope in description.changed_scopes],
            "Changed paths:",
            *[f"- {path}" for path in description.changed_paths],
        ]
        if description.detected_changes:
            lines.extend(["Detected changes:", *[f"- {change}" for change in description.detected_changes]])
        gate_label = "passed" if gates is not None else "not run"
        lines.append(f"Gates: {gate_label}")
        return "\n".join(lines)

    def _require_gates_passed(self, gates: Any | None) -> None:
        if gates is None:
            return
        if bool(getattr(gates, "passed", False)):
            return
        detail = _gate_failure_summary(gates)
        raise CoreRepositoryError(f"local agent edits failed gates: {detail}")

    def _detected_local_changes(self, changed_paths: list[str]) -> list[str]:
        detected: list[str] = []
        for rel in changed_paths:
            if Path(rel).name != "agent.yaml":
                continue
            old = self._read_yaml_from_live(rel)
            new = self._read_yaml_from_worktree(rel)
            prefix = f"{rel}: "
            for change in _semantic_yaml_changes(old, new):
                detected.append(prefix + change)
        return sorted(detected)

    def _read_yaml_from_live(self, rel: str) -> dict[str, Any]:
        result = self._run_git(["show", f"{LIVE_REF}:{rel}"], check=False)
        if result.returncode != 0:
            return {}
        return _yaml_mapping(result.stdout)

    def _read_yaml_from_worktree(self, rel: str) -> dict[str, Any]:
        path = self.agents_root / rel
        if not path.exists() or not path.is_file():
            return {}
        return _yaml_mapping(path.read_text(encoding="utf-8"))

    def _commit_message(self, summary: str, reason: str) -> str:
        lines = [summary.strip() or "update core tree"]
        if reason:
            lines.extend(["", f"Reason: {reason}"])
        return "\n".join(lines)

    def _run_git(
        self,
        args: list[str],
        *,
        work_tree: Path | None = None,
        check: bool = True,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", f"--git-dir={self.git_dir}", *([f"--work-tree={work_tree}"] if work_tree else []), *args]
        return self._run(command[1:], cwd=None, check=check, prefix=["git"], input=input)

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None,
        check: bool = True,
        prefix: list[str] | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [*(prefix or []), *args]
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                text=True,
                input=input,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise CoreRepositoryError("required command not found: git") from exc
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CoreRepositoryError(f"{' '.join(command)} failed: {detail}")
        return completed


@dataclass(slots=True)
class CoreChangeSet:
    repository: CoreRepository
    run_id: str
    kind: str
    run_root: Path
    agents_root: Path

    @property
    def report_path(self) -> Path:
        return self.run_root / "report.md"

    @property
    def proposal_path(self) -> Path:
        return self.run_root / "proposal.json"

    @property
    def gates_path(self) -> Path:
        return self.run_root / "gates.json"

    def changed_paths(self) -> list[str]:
        status_paths = _paths_from_porcelain(self._git(["status", "--porcelain"]).stdout)
        diff_paths = self._git(["diff", "--name-only", LIVE_REF]).stdout.splitlines()
        return sorted({path for path in [*status_paths, *diff_paths] if path})

    def clean_generated_artifacts(self) -> list[str]:
        removed: set[str] = set()
        for rel in self._untracked_paths():
            if not _is_generated_artifact_path(rel):
                continue
            target = self.agents_root / rel
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            removed.add(Path(rel).as_posix())
        self._prune_empty_generated_dirs()
        return sorted(removed)

    def diff_summary(self) -> DiffSummary:
        return DiffSummary(
            changed_paths=self.changed_paths(),
            name_status=self._git(["diff", "--name-status", LIVE_REF]).stdout.strip(),
            stat=self._git(["diff", "--stat", LIVE_REF]).stdout.strip(),
        )

    def commit_proposal(self, *, reason: str, metadata: dict[str, Any] | None = None) -> CommitResult:
        self.clean_generated_artifacts()
        base_revision = self.repository._change_set_base_revision(self.run_id)
        self._git(["add", "-A"])
        worktree_paths = [line.strip() for line in self._git(["diff", "--cached", "--name-only"]).stdout.splitlines() if line.strip()]
        if worktree_paths:
            self._git(["commit", "-m", self.repository._commit_message(f"evolve proposal {self.run_id}", reason)])
        revision = self._git(["rev-parse", "HEAD"]).stdout.strip()
        self.repository._run_git(["update-ref", self.repository.run_ref(self.run_id), revision])
        paths = self.changed_paths()
        payload = {
            "run_id": self.run_id,
            "revision": revision,
            "base_revision": base_revision,
            "changed_paths": paths,
            "metadata": metadata or {},
        }
        write_json(self.proposal_path, payload)
        return CommitResult(revision=revision, previous_revision=base_revision, summary=f"proposal {self.run_id}")

    def discard(self) -> None:
        self.repository._run_git(["worktree", "remove", "--force", str(self.agents_root)], check=False)
        shutil.rmtree(self.run_root, ignore_errors=True)

    def write_report(self, title: str, payload: dict[str, Any]) -> None:
        lines = [f"# {title}", "", "```json", json.dumps(payload, indent=2, ensure_ascii=False), "```", ""]
        self.report_path.write_text("\n".join(lines), encoding="utf-8")

    def _untracked_paths(self) -> list[str]:
        paths: set[str] = set()
        for args in (
            ["ls-files", "--others", "--exclude-standard", "-z"],
            ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        ):
            output = self._git(args).stdout
            paths.update(path for path in output.split("\0") if path)
        return sorted(paths)

    def _prune_empty_generated_dirs(self) -> None:
        directories = [path for path in self.agents_root.rglob("*") if path.is_dir()]
        for path in sorted(directories, key=lambda item: len(item.relative_to(self.agents_root).parts), reverse=True):
            rel = path.relative_to(self.agents_root).as_posix()
            if not _is_generated_artifact_path(rel):
                continue
            try:
                path.rmdir()
            except OSError:
                continue

    def _git(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.agents_root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise CoreRepositoryError("required command not found: git") from exc
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CoreRepositoryError(f"git {' '.join(args)} failed: {detail}")
        return completed


def _paths_from_porcelain(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip())
    return sorted(paths)


def _changed_scopes(changed_paths: list[str]) -> list[str]:
    scopes: set[str] = set()
    for rel in changed_paths:
        parts = Path(rel).parts
        if not parts:
            continue
        if parts[0] == "agent.yaml":
            scopes.add("global fallback")
        else:
            scopes.add(parts[0])
    return sorted(scopes)


def _local_edit_summary(changed_paths: list[str], scopes: list[str], detected: list[str]) -> str:
    if not changed_paths:
        return "no local agent edits"
    if scopes == ["global fallback"]:
        return "update global fallback config"
    core_scopes = [scope for scope in scopes if scope != "global fallback"]
    if len(core_scopes) != 1 or "global fallback" in scopes:
        return "save local agent edits"
    core_id = core_scopes[0]
    if changed_paths == [f"{core_id}/agent.yaml"]:
        semantic = [change.split(": ", 1)[1] if ": " in change else change for change in detected]
        if semantic and all(change.startswith("model.") for change in semantic):
            return f"update {core_id} model config"
        return f"update {core_id} config"
    if changed_paths == [f"{core_id}/agent/SOUL.md"]:
        return f"save {core_id} authored prompt edits"
    if all(path.startswith(f"{core_id}/agent/tools/") for path in changed_paths):
        return f"save {core_id} tool edits"
    if all(path.startswith(f"{core_id}/agent/skills/") for path in changed_paths):
        return f"save {core_id} skill edits"
    if all(path.startswith(f"{core_id}/agent/bootstrap/") for path in changed_paths):
        return f"save {core_id} bootstrap edits"
    if all(path.startswith(f"{core_id}/agent/schedules/") for path in changed_paths):
        return f"save {core_id} schedule edits"
    return f"save {core_id} agent edits"


def _yaml_mapping(content: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _semantic_yaml_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    keys = sorted(set(_flatten_yaml(old)) | set(_flatten_yaml(new)))
    old_flat = _flatten_yaml(old)
    new_flat = _flatten_yaml(new)
    for key in keys:
        if old_flat.get(key) != new_flat.get(key):
            changes.append(f"{key} changed")
    return changes


def _flatten_yaml(value: Any, *, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value} if prefix else {}
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            flattened.update(_flatten_yaml(child, prefix=path))
        elif isinstance(child, list):
            flattened[path] = json.dumps(child, sort_keys=True, ensure_ascii=False)
        else:
            flattened[path] = child
    return flattened


def _gate_failure_summary(gates: Any) -> str:
    phases = getattr(gates, "phases", None)
    if not phases:
        return "unknown gate failure"
    failures = [phase for phase in phases if not bool(getattr(phase, "passed", False))]
    return "; ".join(f"{getattr(phase, 'name', 'gate')}: {getattr(phase, 'detail', '')}" for phase in failures[:5]) or "unknown gate failure"


def _consistency_report_to_dict(report: CoreConsistencyReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "live_revision": report.live_revision,
        "previous_revision": report.previous_revision,
        "checkout_head": report.checkout_head,
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "remediation": issue.remediation,
            }
            for issue in report.issues
        ],
    }


def reject_generated_artifacts(root: Path) -> list[str]:
    rejected: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_generated_artifact_path(rel):
            rejected.append(rel)
    return sorted(rejected)


def _is_generated_artifact_path(rel: str | Path) -> bool:
    path = Path(rel)
    parts = path.parts
    if any(part in GENERATED_ARTIFACT_DIR_NAMES for part in parts):
        return True
    if parts and parts[0] in GENERATED_RUNTIME_DIR_NAMES:
        return True
    return path.suffix in GENERATED_ARTIFACT_SUFFIXES


def reject_dependency_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name in PROTECTED_DEPENDENCY_FILES
    )
