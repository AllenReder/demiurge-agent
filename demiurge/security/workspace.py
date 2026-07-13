from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from demiurge.security.sensitive_paths import (
    CREDENTIAL_DIRECTORY_NAMES,
    CREDENTIAL_FILE_NAMES,
)


DEFAULT_READ_LIMIT_CHARS = 20_000
DEFAULT_TOOL_OUTPUT_LIMIT_CHARS = 12_000


class WorkspaceScopeError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    path: Path
    relative: str
    sensitive: bool
    outside: bool = False


class WorkspaceScope:
    def __init__(
        self,
        root: Path,
        *,
        write_root: Path | None = None,
        read_roots: Iterable[Path] | None = None,
        blocked_write_names: Iterable[str] | None = None,
    ):
        self.root = root.expanduser().resolve()
        self.write_root = (write_root or self.root).expanduser().resolve()
        self.read_roots = [self.root, *[(path.expanduser().resolve()) for path in (read_roots or [])]]
        self.blocked_write_names = {name.lower() for name in (blocked_write_names or [])}

    def resolve_path(
        self,
        value: str | Path | None = None,
        *,
        base: Path | None = None,
        operation: str = "read",
        allow_outside_read: bool = False,
    ) -> ResolvedPath:
        raw = Path(value or ".").expanduser()
        base_path = (base or self.root).expanduser().resolve()
        if not raw.is_absolute():
            raw = base_path / raw
        resolved = raw.expanduser().resolve(strict=False)
        outside = operation == "read" and self._containing_read_root(resolved) is None
        if outside and not allow_outside_read:
            self.require_within_workspace(resolved, operation=operation)
        elif operation != "read":
            self.require_within_workspace(resolved, operation=operation)
        if operation != "read" and resolved.name.lower() in self.blocked_write_names:
            raise WorkspaceScopeError(f"write blocked for protected file: {resolved.name}")
        return ResolvedPath(
            path=resolved,
            relative=self.relative_display(resolved),
            sensitive=self.is_sensitive_path(resolved, operation=operation),
            outside=outside,
        )

    def require_within_workspace(self, path: Path, *, operation: str = "read") -> None:
        resolved = path.resolve(strict=False)
        roots = [self.write_root] if operation != "read" else self.read_roots
        if any(self._is_within_root(resolved, root) for root in roots):
            return
        raise WorkspaceScopeError(f"path escapes workspace: {path}")

    def relative_display(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        for root in [self.root, *self.read_roots]:
            try:
                return resolved.relative_to(root).as_posix()
            except ValueError:
                continue
        return str(path)

    def is_sensitive_path(self, path: Path, *, operation: str = "read") -> bool:
        resolved = path.resolve(strict=False)
        root = self.write_root if operation != "read" else self._containing_read_root(resolved)
        if root is None:
            lowered = [name.lower() for name in resolved.parts]
        else:
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                lowered = [name.lower() for name in resolved.parts]
            else:
                lowered = [name.lower() for name in relative.parts]
        if any(
            name in {".git", ".venv", ".demiurge"}
            or name in CREDENTIAL_DIRECTORY_NAMES
            for name in lowered
        ):
            return True
        if any(name.startswith(".env") for name in lowered):
            return True
        if lowered and lowered[-1] in CREDENTIAL_FILE_NAMES:
            return True
        if self._looks_like_private_key(lowered):
            return True
        if operation != "read" and lowered and lowered[-1] in {"pyproject.toml", "uv.lock"}:
            return True
        return False

    def _containing_read_root(self, path: Path) -> Path | None:
        for root in self.read_roots:
            if self._is_within_root(path, root):
                return root
        return None

    def _is_within_root(self, path: Path, root: Path) -> bool:
        if path == root:
            return True
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def contains_sensitive_children(self, path: Path, *, operation: str = "read") -> bool:
        resolved = path.resolve(strict=False)
        if self.is_sensitive_path(resolved, operation=operation):
            return True
        if not resolved.exists() or not resolved.is_dir():
            return False
        for child in self._walk_limited(resolved, limit=500):
            if self.is_sensitive_path(child, operation=operation):
                return True
        return False

    def _walk_limited(self, root: Path, *, limit: int) -> Iterable[Path]:
        count = 0
        for child in root.rglob("*"):
            count += 1
            if count > limit:
                return
            yield child

    def _looks_like_private_key(self, lowered_parts: list[str]) -> bool:
        if not lowered_parts:
            return False
        name = lowered_parts[-1]
        private_key_names = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
        if name in private_key_names:
            return True
        if name.endswith((".pem", ".key")):
            return True
        return "private" in name and "key" in name


def truncate_text(text: str, *, limit: int = DEFAULT_TOOL_OUTPUT_LIMIT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n...[truncated {omitted} chars]"
