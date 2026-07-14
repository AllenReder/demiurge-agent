from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO, Iterator


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_DIRECTORY_FD_SUPPORTED = (
    os.name != "nt"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
)
_ATOMIC_DIRECTORY_FD_SUPPORTED = (
    _DIRECTORY_FD_SUPPORTED
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


@dataclass(frozen=True, slots=True)
class RuntimePermissionIssue:
    path: str
    kind: str
    expected_mode: str
    actual_mode: str | None
    reason: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "path": self.path,
            "kind": self.kind,
            "expected_mode": self.expected_mode,
            "actual_mode": self.actual_mode,
            "reason": self.reason,
        }


class RuntimePermissionError(PermissionError):
    def __init__(self, issues: tuple[RuntimePermissionIssue, ...]):
        self.issues = issues
        detail = "; ".join(
            f"{item.path}: {item.reason}"
            for item in issues[:10]
        )
        if len(issues) > 10:
            detail += f"; ... {len(issues) - 10} more"
        super().__init__(f"private runtime permission enforcement failed: {detail}")


def ensure_private_directory(path: Path) -> Path:
    path = path.expanduser()
    if _supports_directory_fds():
        with _open_private_directory_fd(path, create=True) as (
            descriptor,
            absolute_path,
        ):
            _assert_path_matches_descriptor(
                absolute_path,
                descriptor,
                kind="directory",
                expected=PRIVATE_DIRECTORY_MODE,
                reason="private runtime directory changed during creation",
            )
        return path
    return _ensure_private_directory_by_path(path)


def _ensure_private_directory_by_path(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise RuntimePermissionError(
                (
                    _issue(
                        candidate,
                        kind="directory",
                        expected=PRIVATE_DIRECTORY_MODE,
                        actual=None,
                        reason=(
                            "symbolic links are not accepted in private runtime "
                            "directory ancestry"
                        ),
                    ),
                )
            )
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if cursor.is_symlink():
        raise RuntimePermissionError(
            (
                _issue(
                    cursor,
                    kind="directory",
                    expected=PRIVATE_DIRECTORY_MODE,
                    actual=None,
                    reason=(
                        "symbolic links are not accepted in private runtime "
                        "directory ancestry"
                    ),
                ),
            )
        )
    if cursor.exists() and not cursor.is_dir():
        raise NotADirectoryError(cursor)
    for directory in reversed(missing):
        directory.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimePermissionError(
                (
                    _issue(
                        directory,
                        kind="directory",
                        expected=PRIVATE_DIRECTORY_MODE,
                        actual=None,
                        reason="private runtime directory changed during creation",
                    ),
                )
            )
        _set_private_mode(directory, PRIVATE_DIRECTORY_MODE)
    if not path.is_dir():
        raise NotADirectoryError(path)
    _set_private_mode(path, PRIVATE_DIRECTORY_MODE)
    return path


def _supports_directory_fds() -> bool:
    return _DIRECTORY_FD_SUPPORTED


@contextmanager
def _open_private_directory_fd(
    path: Path,
    *,
    create: bool,
) -> Iterator[tuple[int, Path]]:
    absolute_path = Path(os.path.abspath(os.fspath(path.expanduser())))
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(os.sep, flags)
    try:
        parts = absolute_path.parts[1:]
        for index, part in enumerate(parts):
            candidate = Path(os.sep, *parts[: index + 1])
            created = False
            try:
                metadata = os.stat(
                    part,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, PRIVATE_DIRECTORY_MODE, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
                metadata = os.stat(
                    part,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            _validate_private_directory_metadata(candidate, metadata)
            child_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child_descriptor
            if created or index == len(parts) - 1:
                os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
        yield descriptor, absolute_path
    finally:
        os.close(descriptor)


def _validate_private_directory_metadata(
    path: Path,
    metadata: os.stat_result,
) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimePermissionError(
            (
                _issue(
                    path,
                    kind="directory",
                    expected=PRIVATE_DIRECTORY_MODE,
                    actual=None,
                    reason=(
                        "symbolic links are not accepted in private runtime "
                        "directory ancestry"
                    ),
                ),
            )
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError(path)


def _assert_path_matches_descriptor(
    path: Path,
    descriptor: int,
    *,
    kind: str,
    expected: int,
    reason: str,
) -> None:
    descriptor_metadata = os.fstat(descriptor)
    try:
        path_metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise RuntimePermissionError(
            (
                _issue(
                    path,
                    kind=kind,
                    expected=expected,
                    actual=None,
                    reason=reason,
                ),
            )
        ) from exc
    if (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ) != (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
    ):
        raise RuntimePermissionError(
            (
                _issue(
                    path,
                    kind=kind,
                    expected=expected,
                    actual=stat.S_IMODE(path_metadata.st_mode),
                    reason=reason,
                ),
            )
        )


def ensure_private_file(path: Path) -> Path:
    if _DIRECTORY_FD_SUPPORTED:
        absolute_path = Path(os.path.abspath(os.fspath(path.expanduser())))
        with _open_private_directory_fd(
            absolute_path.parent,
            create=False,
        ) as (parent_descriptor, _):
            metadata = os.stat(
                absolute_path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimePermissionError(
                    (
                        _issue(
                            absolute_path,
                            kind="file",
                            expected=PRIVATE_FILE_MODE,
                            actual=None,
                            reason=(
                                "symbolic links are not accepted for private "
                                "runtime files"
                            ),
                        ),
                    )
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise IsADirectoryError(absolute_path)
            descriptor = os.open(
                absolute_path.name,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise IsADirectoryError(absolute_path)
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
                _assert_path_matches_descriptor(
                    absolute_path,
                    descriptor,
                    kind="file",
                    expected=PRIVATE_FILE_MODE,
                    reason="private runtime file changed during permission tightening",
                )
            finally:
                os.close(descriptor)
        return path
    return _ensure_private_file_by_path(path)


def _ensure_private_file_by_path(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimePermissionError(
            (
                _issue(
                    path,
                    kind="file",
                    expected=PRIVATE_FILE_MODE,
                    actual=None,
                    reason="symbolic links are not accepted for private runtime files",
                ),
            )
        )
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise IsADirectoryError(path)
    _set_private_mode(path, PRIVATE_FILE_MODE)
    return path


def open_private_text(
    path: Path,
    mode: str,
    *,
    encoding: str = "utf-8",
    errors: str | None = None,
    buffering: int = -1,
) -> IO[str]:
    if mode not in {"a", "w", "x"}:
        raise ValueError("private text files support only a, w, or x mode")
    if not _DIRECTORY_FD_SUPPORTED:
        return _open_private_text_by_path(
            path,
            mode,
            encoding=encoding,
            errors=errors,
            buffering=buffering,
        )
    absolute_path = Path(os.path.abspath(os.fspath(path.expanduser())))
    flags = _private_text_open_flags(mode) | os.O_NOFOLLOW
    with _open_private_directory_fd(
        absolute_path.parent,
        create=True,
    ) as (parent_descriptor, _):
        descriptor = os.open(
            absolute_path.name,
            flags,
            PRIVATE_FILE_MODE,
            dir_fd=parent_descriptor,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("private runtime file is not a regular file")
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            _assert_path_matches_descriptor(
                absolute_path,
                descriptor,
                kind="file",
                expected=PRIVATE_FILE_MODE,
                reason="private runtime file changed during open",
            )
            stream = os.fdopen(
                descriptor,
                mode,
                encoding=encoding,
                errors=errors,
                buffering=buffering,
            )
            descriptor = -1
            return stream
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _open_private_text_by_path(
    path: Path,
    mode: str,
    *,
    encoding: str,
    errors: str | None,
    buffering: int,
) -> IO[str]:
    ensure_private_directory(path.parent)
    if path.is_symlink():
        raise RuntimePermissionError(
            (
                _issue(
                    path,
                    kind="file",
                    expected=PRIVATE_FILE_MODE,
                    actual=None,
                    reason="symbolic links are not accepted for private runtime files",
                ),
            )
        )
    flags = _private_text_open_flags(mode)
    if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        if os.name != "nt" and not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("private runtime file is not a regular file")
        if hasattr(os, "fchmod") and os.name != "nt":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        stream = os.fdopen(
            descriptor,
            mode,
            encoding=encoding,
            errors=errors,
            buffering=buffering,
        )
        descriptor = -1
        return stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _private_text_open_flags(mode: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT
    if mode == "a":
        return flags | os.O_APPEND
    if mode == "w":
        return flags | os.O_TRUNC
    return flags | os.O_EXCL


def append_private_jsonl(path: Path, value: Any) -> None:
    with open_private_text(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write_private_text(
    path: Path,
    value: str,
    *,
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    if not _ATOMIC_DIRECTORY_FD_SUPPORTED:
        _atomic_write_private_text_by_path(path, value, mode=mode)
        return
    absolute_path = Path(os.path.abspath(os.fspath(path.expanduser())))
    target_name = absolute_path.name
    if not target_name:
        raise IsADirectoryError(absolute_path)
    with _open_private_directory_fd(
        absolute_path.parent,
        create=True,
    ) as (parent_descriptor, _):
        temporary_name = (
            f".{target_name}.{secrets.token_hex(12)}.tmp"
        )
        file_descriptor = -1
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
            )
            file_descriptor = os.open(
                temporary_name,
                flags,
                mode,
                dir_fd=parent_descriptor,
            )
            os.fchmod(file_descriptor, mode)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                file_descriptor = -1
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temporary_name,
                target_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            target_descriptor = os.open(
                target_name,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            try:
                _assert_path_matches_descriptor(
                    absolute_path,
                    target_descriptor,
                    kind="file",
                    expected=mode,
                    reason="private runtime file changed during atomic write",
                )
            finally:
                os.close(target_descriptor)
            os.fsync(parent_descriptor)
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass


def _atomic_write_private_text_by_path(
    path: Path,
    value: str,
    *,
    mode: int,
) -> None:
    ensure_private_directory(path.parent)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        if hasattr(os, "fchmod") and os.name != "nt":
            os.fchmod(file_descriptor, mode)
        elif os.name != "nt":
            os.chmod(temporary_path, mode)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            file_descriptor = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        if os.name != "nt":
            os.chmod(path, mode, follow_symlinks=False)
        _fsync_directory_by_path(path.parent)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary_path.unlink(missing_ok=True)


def _fsync_directory_by_path(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_descriptor = os.open(path, flags)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def audit_runtime_permissions(
    home: Path,
) -> tuple[RuntimePermissionIssue, ...]:
    if os.name == "nt" or not home.exists():
        return ()
    issues: list[RuntimePermissionIssue] = []
    for path, kind, expected in _runtime_permission_targets(home):
        try:
            if path.is_symlink():
                issues.append(
                    _issue(
                        path,
                        kind=kind,
                        expected=expected,
                        actual=None,
                        reason="symbolic link is not allowed in the private runtime tree",
                    )
                )
                continue
            actual = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            issues.append(
                _issue(
                    path,
                    kind=kind,
                    expected=expected,
                    actual=None,
                    reason=f"could not inspect mode ({type(exc).__name__})",
                )
            )
            continue
        if actual != expected:
            issues.append(
                _issue(
                    path,
                    kind=kind,
                    expected=expected,
                    actual=actual,
                    reason="mode is broader or different from the Host private runtime policy",
                )
            )
    return tuple(issues)


def tighten_runtime_permissions(
    home: Path,
) -> tuple[RuntimePermissionIssue, ...]:
    if os.name == "nt":
        ensure_private_directory(home)
        return ()
    try:
        ensure_private_directory(home)
    except (OSError, RuntimePermissionError) as exc:
        return (
            _issue(
                home,
                kind="directory",
                expected=PRIVATE_DIRECTORY_MODE,
                actual=None,
                reason=f"could not secure runtime home ({type(exc).__name__})",
            ),
        )
    failures: list[RuntimePermissionIssue] = []
    for path, kind, expected in _runtime_permission_targets(home):
        try:
            if kind == "directory":
                ensure_private_directory(path)
            else:
                ensure_private_file(path)
        except (OSError, RuntimePermissionError) as exc:
            actual = None
            try:
                actual = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                pass
            failures.append(
                _issue(
                    path,
                    kind=kind,
                    expected=expected,
                    actual=actual,
                    reason=f"could not tighten mode ({type(exc).__name__})",
                )
            )
    return tuple(failures)


def require_private_runtime_permissions(home: Path) -> None:
    failures = tighten_runtime_permissions(home)
    if failures:
        raise RuntimePermissionError(failures)


def _runtime_permission_targets(
    home: Path,
) -> list[tuple[Path, str, int]]:
    targets: list[tuple[Path, str, int]] = []
    if home.exists():
        targets.append((home, "directory", PRIVATE_DIRECTORY_MODE))
    for file_path in (home / ".env", home / "config.yaml"):
        if file_path.exists() or file_path.is_symlink():
            targets.append((file_path, "file", PRIVATE_FILE_MODE))
    for root in (home / "runtime", home / "logs", home / "state"):
        if not root.exists() and not root.is_symlink():
            continue
        if root.is_symlink():
            targets.append((root, "directory", PRIVATE_DIRECTORY_MODE))
            continue
        for current_root, directory_names, file_names in os.walk(
            root,
            followlinks=False,
        ):
            current = Path(current_root)
            targets.append((current, "directory", PRIVATE_DIRECTORY_MODE))
            for name in directory_names:
                candidate = current / name
                if candidate.is_symlink():
                    targets.append(
                        (candidate, "directory", PRIVATE_DIRECTORY_MODE)
                    )
            for name in file_names:
                targets.append(
                    (current / name, "file", PRIVATE_FILE_MODE)
                )
    unique: dict[str, tuple[Path, str, int]] = {}
    for item in targets:
        unique[str(item[0])] = item
    return list(unique.values())


def _set_private_mode(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    os.chmod(path, mode, follow_symlinks=False)


def _issue(
    path: Path,
    *,
    kind: str,
    expected: int,
    actual: int | None,
    reason: str,
) -> RuntimePermissionIssue:
    return RuntimePermissionIssue(
        path=str(path),
        kind=kind,
        expected_mode=oct(expected),
        actual_mode=oct(actual) if actual is not None else None,
        reason=reason,
    )
