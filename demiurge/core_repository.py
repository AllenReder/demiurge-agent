from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

if os.name != "nt":
    import fcntl
else:  # pragma: no cover - Windows keeps the interface but not flock semantics.
    fcntl = None  # type: ignore[assignment]

from demiurge.util import ensure_dir, utc_id, write_json


LIVE_REF = "refs/demiurge/live"
PREVIOUS_REF = "refs/demiurge/previous"
RUN_REF_PREFIX = "refs/demiurge/runs"
PROTECTED_DEPENDENCY_FILES = {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.in"}


class CoreRepositoryError(RuntimeError):
    pass


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
            self._run_git(["add", "-A"], work_tree=self.agents_root)
            self._run_git(["commit", "-m", self._commit_message("core init", reason)], work_tree=self.agents_root)
            revision = self._run_git(["rev-parse", "HEAD"], work_tree=self.agents_root).stdout.strip()
            self._run_git(["update-ref", LIVE_REF, revision])
            self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)
            return CorePointer(core_id="agents", active_revision=revision, previous_revision=None, reason=reason)

    def ensure_initialized(self, source_agents: Path) -> None:
        if self.git_dir.exists():
            return
        self.initialize_from_source(source_agents, reason="auto init", force=False)

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
        return {
            "agents_root": str(self.agents_root),
            "git_dir": str(self.git_dir),
            "live": self.live_revision(),
            "previous": self.previous_revision(),
            "dirty": bool(self.live_changed_paths()),
            "changed_paths": self.live_changed_paths(),
        }

    def live_changed_paths(self) -> list[str]:
        self.require_initialized()
        result = self._run_git(["status", "--porcelain"], work_tree=self.agents_root)
        return _paths_from_porcelain(result.stdout)

    def require_live_clean(self) -> None:
        changed = self.live_changed_paths()
        if changed:
            raise CoreRepositoryError(f"live agents tree has uncommitted changes: {changed}")

    def begin_change_set(self, *, kind: str, reason: str, run_id: str | None = None) -> "CoreChangeSet":
        self.require_initialized()
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
            self.require_live_clean()
            ref = self.run_ref(run_id)
            proposal = self._run_git(["rev-parse", "--verify", ref]).stdout.strip()
            previous = self.live_revision()
            self._run_git(["update-ref", PREVIOUS_REF, previous])
            self._run_git(["update-ref", LIVE_REF, proposal])
            self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)
            return CommitResult(revision=proposal, previous_revision=previous, summary=f"promoted {run_id} to live")

    def commit_live(self, *, reason: str, summary: str) -> CommitResult:
        self.require_initialized()
        previous = self.live_revision()
        self._run_git(["add", "-A"], work_tree=self.agents_root)
        if not self.live_changed_paths():
            return CommitResult(revision=previous, previous_revision=self.previous_revision(), summary="no live changes to commit")
        self._run_git(["commit", "-m", self._commit_message(summary, reason)], work_tree=self.agents_root)
        revision = self._run_git(["rev-parse", "HEAD"], work_tree=self.agents_root).stdout.strip()
        self._run_git(["update-ref", PREVIOUS_REF, previous])
        self._run_git(["update-ref", LIVE_REF, revision])
        return CommitResult(revision=revision, previous_revision=previous, summary=summary)

    def rollback(self, target: str = "previous", *, reason: str = "rollback") -> CommitResult:
        self.require_initialized()
        with self.locked():
            self.require_live_clean()
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
            self._run_git(["update-ref", PREVIOUS_REF, current])
            self._run_git(["update-ref", LIVE_REF, commit])
            self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)
            return CommitResult(revision=commit, previous_revision=current, summary=f"rolled back to {target_revision}")

    @contextmanager
    def locked(self) -> Iterator[None]:
        ensure_dir(self.home)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise CoreRepositoryError(f"core repository is locked: {self.lock_path}") from exc
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def live_transaction(self, *, reason: str) -> Iterator[Path]:
        with self.locked():
            self.require_live_clean()
            try:
                yield self.agents_root
            except BaseException:
                self.reset_live()
                raise

    def reset_live(self) -> None:
        self.require_initialized()
        self._run_git(["reset", "--hard", LIVE_REF], work_tree=self.agents_root)

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
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", f"--git-dir={self.git_dir}", *([f"--work-tree={work_tree}"] if work_tree else []), *args]
        return self._run(command[1:], cwd=None, check=check, prefix=["git"])

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None,
        check: bool = True,
        prefix: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [*(prefix or []), *args]
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                text=True,
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

    def diff_summary(self) -> DiffSummary:
        return DiffSummary(
            changed_paths=self.changed_paths(),
            name_status=self._git(["diff", "--name-status", LIVE_REF]).stdout.strip(),
            stat=self._git(["diff", "--stat", LIVE_REF]).stdout.strip(),
        )

    def commit_proposal(self, *, reason: str, metadata: dict[str, Any] | None = None) -> CommitResult:
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
            "base_revision": self.repository.live_revision(),
            "changed_paths": paths,
            "metadata": metadata or {},
        }
        write_json(self.proposal_path, payload)
        return CommitResult(revision=revision, previous_revision=self.repository.live_revision(), summary=f"proposal {self.run_id}")

    def discard(self) -> None:
        self.repository._run_git(["worktree", "remove", "--force", str(self.agents_root)], check=False)
        shutil.rmtree(self.run_root, ignore_errors=True)

    def write_report(self, title: str, payload: dict[str, Any]) -> None:
        lines = [f"# {title}", "", "```json", json.dumps(payload, indent=2, ensure_ascii=False), "```", ""]
        self.report_path.write_text("\n".join(lines), encoding="utf-8")

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


def reject_generated_artifacts(root: Path) -> list[str]:
    rejected: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            rejected.append(rel)
        elif path.name in {".pytest_cache"} or ".pytest_cache" in path.parts:
            rejected.append(rel)
        elif rel.startswith((".evolve/", "runtime/", "logs/", "runs/", "history/", "registry/")):
            rejected.append(rel)
    return sorted(rejected)


def reject_dependency_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name in PROTECTED_DEPENDENCY_FILES
    )
