from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_READ_LIMIT_CHARS = 20_000
DEFAULT_TOOL_OUTPUT_LIMIT_CHARS = 12_000


class WorkspaceScopeError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    path: Path
    relative: str
    sensitive: bool


class WorkspaceScope:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    def resolve_path(
        self,
        value: str | Path | None = None,
        *,
        base: Path | None = None,
        operation: str = "read",
    ) -> ResolvedPath:
        raw = Path(value or ".")
        base_path = (base or self.root).expanduser().resolve()
        if not raw.is_absolute():
            raw = base_path / raw
        resolved = raw.expanduser().resolve(strict=False)
        self.require_within_workspace(resolved)
        return ResolvedPath(
            path=resolved,
            relative=self.relative_display(resolved),
            sensitive=self.is_sensitive_path(resolved, operation=operation),
        )

    def require_within_workspace(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceScopeError(f"path escapes workspace: {path}") from exc

    def relative_display(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def is_sensitive_path(self, path: Path, *, operation: str = "read") -> bool:
        resolved = path.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError:
            return True
        names = list(relative.parts)
        lowered = [name.lower() for name in names]
        if any(name in {".git", ".ssh", ".venv", ".demiurge"} for name in lowered):
            return True
        if any(name.startswith(".env") for name in lowered):
            return True
        if self._looks_like_private_key(lowered):
            return True
        if operation != "read" and lowered and lowered[-1] in {"pyproject.toml", "uv.lock"}:
            return True
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
