from __future__ import annotations

import asyncio
import codecs
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from demiurge.security.private_files import open_private_text


_PROCESS_GROUP_GRACE_SECONDS = 0.25
_IS_POSIX = os.name == "posix"
_IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    spawn_id: str
    process_group_id: int | None
    platform: str
    start_identity: str | None = None

    def audit_view(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "spawn_id": self.spawn_id,
            "process_group_id": self.process_group_id,
            "platform": self.platform,
            "start_identity": self.start_identity,
        }


class ProcessTimeoutExpired(subprocess.TimeoutExpired):
    def __init__(
        self,
        command: str,
        timeout_seconds: float,
        *,
        output: str,
        stderr: str,
        process_identity: ProcessIdentity,
        output_stats: dict[str, dict[str, object]],
        output_artifacts: dict[str, str],
    ) -> None:
        super().__init__(
            command,
            timeout_seconds,
            output=output,
            stderr=stderr,
        )
        self.process_identity = process_identity
        self.output_stats = output_stats
        self.output_artifacts = output_artifacts


class ProcessExecutionCancelled(Exception):
    pass


class ProcessIdentityUnavailable(RuntimeError):
    pass


class ProcessTreeTerminationError(RuntimeError):
    pass


class ProcessOutputArtifactError(RuntimeError):
    def __init__(
        self,
        failures: Mapping[str, BaseException],
    ) -> None:
        self.failures = {
            label: type(error).__name__
            for label, error in failures.items()
        }
        details = ", ".join(
            f"{label}={error_type}"
            for label, error_type in sorted(self.failures.items())
        )
        super().__init__(f"terminal output artifact persistence failed: {details}")


class ProcessLifecycleClosed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedProcessOutput:
    stdout: str
    stderr: str
    stats: dict[str, dict[str, object]]
    artifacts: dict[str, str]


class ProcessLifecycleOwner:
    def __init__(self) -> None:
        self._foreground: dict[
            str,
            tuple[threading.Event, asyncio.Task[Any]],
        ] = {}
        self._closed = False

    def ensure_open(self) -> None:
        if self._closed:
            raise ProcessLifecycleClosed("process lifecycle owner is closed")

    def track_foreground(
        self,
        *,
        cancel_event: threading.Event,
        task: asyncio.Task[Any],
    ) -> str:
        self.ensure_open()
        registration_id = f"foreground_{uuid.uuid4().hex}"
        self._foreground[registration_id] = (cancel_event, task)
        return registration_id

    def release_foreground(self, registration_id: str) -> None:
        self._foreground.pop(registration_id, None)

    async def shutdown(self) -> None:
        self._closed = True
        registrations = list(self._foreground.items())
        if not registrations:
            return
        for _, (cancel_event, _) in registrations:
            cancel_event.set()
        await asyncio.gather(
            *(asyncio.shield(task) for _, (_, task) in registrations),
            return_exceptions=True,
        )
        for registration_id, _ in registrations:
            self._foreground.pop(registration_id, None)


def capture_process_identity(pid: int) -> ProcessIdentity:
    platform = "windows" if _IS_WINDOWS else "posix" if _IS_POSIX else "other"
    return ProcessIdentity(
        pid=pid,
        spawn_id=f"proc_{time.time_ns()}_{uuid.uuid4().hex}",
        process_group_id=pid if _IS_POSIX or _IS_WINDOWS else None,
        platform=platform,
        start_identity=_read_process_start_identity(pid),
    )


def bind_process_identity(
    process: object,
    identity: ProcessIdentity,
) -> None:
    if (_IS_POSIX or _IS_WINDOWS) and identity.start_identity is None:
        raise ProcessIdentityUnavailable(
            f"OS process start identity is unavailable for pid {identity.pid}"
        )
    setattr(process, "_demiurge_process_identity", identity)
    if _IS_WINDOWS:
        windows_job = _create_windows_job(
            identity.pid,
            process_handle=_windows_process_handle(process),
        )
        if windows_job is None:
            raise ProcessTreeTerminationError(
                f"could not assign pid {identity.pid} to a Windows Job Object"
            )
        setattr(process, "_demiurge_windows_job", windows_job)
        if not _resume_windows_process(process):
            windows_job.terminate()
            raise ProcessTreeTerminationError(
                f"could not resume Job-owned Windows process {identity.pid}"
            )


def release_process_resources(process: object) -> None:
    _release_bound_windows_job(process)


def process_group_spawn_kwargs() -> dict[str, object]:
    if _IS_POSIX:
        return {"start_new_session": True}
    if _IS_WINDOWS:
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_SUSPENDED", 0)
        )
        if creation_flags:
            return {"creationflags": creation_flags}
    return {}


def run_foreground_process(
    command: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    output_limit_chars: int,
    cancel_event: threading.Event | None = None,
    output_artifact_paths: Mapping[str, Path] | None = None,
    artifact_redactions: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(env),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **process_group_spawn_kwargs(),
    )
    assert process.stdout is not None
    assert process.stderr is not None
    identity = capture_process_identity(process.pid)
    try:
        bind_process_identity(process, identity)
    except Exception:
        try:
            process.kill()
            process.wait()
        finally:
            _close_pipe(process.stdout)
            _close_pipe(process.stderr)
        raise
    stdout_tail = _BoundedTextTail(output_limit_chars)
    stderr_tail = _BoundedTextTail(output_limit_chars)
    artifact_paths = dict(output_artifact_paths or {})
    redactions = {
        value: f"<redacted:{target}>"
        for target, value in (artifact_redactions or {}).items()
        if value
    }
    drains = [
        _start_drain_thread(
            process.stdout,
            stdout_tail,
            label="stdout",
            artifact_path=artifact_paths.get("stdout"),
            redactions=redactions,
        ),
        _start_drain_thread(
            process.stderr,
            stderr_tail,
            label="stderr",
            artifact_path=artifact_paths.get("stderr"),
            redactions=redactions,
        ),
    ]
    deadline = time.monotonic() + timeout_seconds
    termination_reason: str | None = None
    while True:
        process_exited = process.poll() is not None
        output_drained = not any(drain.is_alive() for drain in drains)
        if process_exited and output_drained:
            break
        if cancel_event is not None and cancel_event.is_set():
            termination_reason = "cancelled"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            termination_reason = "timed_out"
            break
        if process_exited:
            _join_drain_threads(
                drains,
                timeout_seconds=min(0.05, remaining),
            )
            continue
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            pass
    if termination_reason is not None:
        _terminate_process_tree(process, identity=identity)
        process.wait()
        _close_pipe(process.stdout)
        _close_pipe(process.stderr)
        _join_drain_threads(drains, timeout_seconds=1)
        release_process_resources(process)
        _raise_output_artifact_error(drains)
        if termination_reason == "cancelled":
            raise ProcessExecutionCancelled()
        raise ProcessTimeoutExpired(
            command,
            timeout_seconds,
            output=stdout_tail.render(),
            stderr=stderr_tail.render(),
            process_identity=identity,
            output_stats={
                "stdout": stdout_tail.stats(),
                "stderr": stderr_tail.stats(),
            },
            output_artifacts=_existing_artifact_paths(artifact_paths),
        ) from None
    _join_drain_threads(drains)
    release_process_resources(process)
    _raise_output_artifact_error(drains)
    completed = subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout_tail.render(),
        stderr=stderr_tail.render(),
    )
    completed.process_identity = identity
    completed.output_stats = {
        "stdout": stdout_tail.stats(),
        "stderr": stderr_tail.stats(),
    }
    completed.output_artifacts = _existing_artifact_paths(artifact_paths)
    return completed


async def terminate_async_process_tree(
    process: asyncio.subprocess.Process,
    *,
    identity: ProcessIdentity | None = None,
) -> None:
    if identity is not None and not _process_identity_matches(process, identity):
        return
    if _IS_POSIX:
        process_group_id = (
            identity.process_group_id
            if identity is not None and identity.process_group_id is not None
            else process.pid
        )
        if not _signal_process_group(process_group_id, signal.SIGTERM):
            return
        deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not _process_group_exists(process_group_id):
                break
            await asyncio.sleep(0.01)
        if _process_group_exists(process_group_id):
            _signal_process_group(process_group_id, signal.SIGKILL)
        await process.wait()
        return
    if _IS_WINDOWS:
        if _terminate_bound_windows_job(process):
            await process.wait()
            return
        if process.returncode is not None:
            return
        terminated = await asyncio.to_thread(
            _terminate_windows_process_tree,
            process.pid,
        )
        if not terminated and process.returncode is None:
            process.kill()
        await process.wait()
        return
    if not _IS_POSIX:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        return


async def drain_async_process_output(
    process: asyncio.subprocess.Process,
    *,
    output_limit_chars: int,
    chunk_size: int = 8 * 1024,
    on_chunk: Callable[[str, str], None] | None = None,
    output_artifact_paths: Mapping[str, Path] | None = None,
) -> BoundedProcessOutput:
    stdout_tail = _BoundedTextTail(output_limit_chars)
    stderr_tail = _BoundedTextTail(output_limit_chars)
    artifact_paths = dict(output_artifact_paths or {})
    artifact_failures: dict[str, BaseException] = {}

    async def drain_stream(
        stream: asyncio.StreamReader | None,
        label: str,
        tail: _BoundedTextTail,
    ) -> None:
        if stream is None:
            return
        sink: _TextArtifactSink | None = None
        try:
            try:
                sink = _TextArtifactSink(artifact_paths.get(label))
            except (OSError, ValueError) as exc:
                artifact_failures.setdefault(label, exc)
            while True:
                chunk = await stream.read(chunk_size)
                if not chunk:
                    break
                text = tail.append(chunk)
                if sink is not None:
                    try:
                        sink.append(text)
                    except (OSError, ValueError) as exc:
                        artifact_failures.setdefault(label, exc)
                        try:
                            sink.close()
                        except (OSError, ValueError) as close_exc:
                            artifact_failures.setdefault(label, close_exc)
                        sink = None
                if on_chunk is not None and text:
                    on_chunk(label, text)
            final_text = tail.finish()
            if sink is not None:
                try:
                    sink.append(final_text)
                except (OSError, ValueError) as exc:
                    artifact_failures.setdefault(label, exc)
            if on_chunk is not None and final_text:
                on_chunk(label, final_text)
        finally:
            if sink is not None:
                try:
                    sink.close()
                except (OSError, ValueError) as exc:
                    artifact_failures.setdefault(label, exc)

    await asyncio.gather(
        drain_stream(process.stdout, "stdout", stdout_tail),
        drain_stream(process.stderr, "stderr", stderr_tail),
        process.wait(),
    )
    if artifact_failures:
        raise ProcessOutputArtifactError(artifact_failures)
    return BoundedProcessOutput(
        stdout=stdout_tail.render(),
        stderr=stderr_tail.render(),
        stats={
            "stdout": stdout_tail.stats(),
            "stderr": stderr_tail.stats(),
        },
        artifacts=_existing_artifact_paths(artifact_paths),
    )


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    *,
    identity: ProcessIdentity,
) -> None:
    if not _process_identity_matches(process, identity):
        return
    if _IS_POSIX:
        process_group_id = identity.process_group_id or process.pid
        if not _signal_process_group(process_group_id, signal.SIGTERM):
            return
        deadline = time.monotonic() + _PROCESS_GROUP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not _process_group_exists(process_group_id):
                return
            time.sleep(0.01)
        _signal_process_group(process_group_id, signal.SIGKILL)
        return
    if _IS_WINDOWS:
        if _terminate_bound_windows_job(process):
            return
        if process.poll() is not None:
            return
        if not _terminate_windows_process_tree(process.pid):
            process.kill()
        return
    if process.poll() is not None:
        return
    process.kill()


def _signal_process_group(process_group_id: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(process_group_id, sig)
    except (PermissionError, ProcessLookupError):
        return False
    return True


def _process_identity_matches(
    process: object,
    identity: ProcessIdentity,
) -> bool:
    if getattr(process, "pid", None) != identity.pid:
        return False
    current_identity = getattr(process, "_demiurge_process_identity", None)
    if current_identity != identity:
        return False
    if identity.start_identity is None:
        return True
    current_start_identity = _read_process_start_identity(identity.pid)
    if current_start_identity is not None:
        return current_start_identity == identity.start_identity
    if _IS_WINDOWS and getattr(process, "_demiurge_windows_job", None) is not None:
        return True
    process_exited = getattr(process, "returncode", None) is not None
    poll = getattr(process, "poll", None)
    if callable(poll):
        process_exited = poll() is not None
    return bool(
        _IS_POSIX
        and process_exited
        and identity.process_group_id is not None
        and _process_group_exists(identity.process_group_id)
    )


def _read_process_start_identity(pid: int) -> str | None:
    if _IS_WINDOWS:
        return _read_windows_process_start_identity(pid)
    if not _IS_POSIX:
        return None
    if sys.platform.startswith("linux"):
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            closing_paren = stat_text.rfind(")")
            fields = stat_text[closing_paren + 2 :].split()
            start_ticks = fields[19]
            boot_id_path = Path("/proc/sys/kernel/random/boot_id")
            boot_id = (
                boot_id_path.read_text(encoding="utf-8").strip()
                if boot_id_path.exists()
                else "unknown-boot"
            )
        except (IndexError, OSError, ValueError):
            return None
        return f"linux:{boot_id}:{start_ticks}"
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    started_at = completed.stdout.strip()
    return f"posix:{started_at}" if completed.returncode == 0 and started_at else None


def _read_windows_process_start_identity(pid: int) -> str | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", wintypes.DWORD),
                ("high", wintypes.DWORD),
            ]

        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        creation = FileTime()
        exit_time = FileTime()
        kernel = FileTime()
        user = FileTime()
        try:
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
        finally:
            kernel32.CloseHandle(handle)
        value = (int(creation.high) << 32) | int(creation.low)
        return f"windows:{value}"
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class _WindowsJob:
    def __init__(self, kernel32: Any, handle: Any) -> None:
        self._kernel32 = kernel32
        self._handle = handle

    def terminate(self) -> None:
        handle = self._handle
        if not handle:
            return
        terminated = self._kernel32.TerminateJobObject(handle, 1)
        self.close()
        if not terminated:
            raise ProcessTreeTerminationError(
                "TerminateJobObject failed for the owned process tree"
            )

    def close(self) -> None:
        handle = self._handle
        if not handle:
            return
        self._handle = None
        if not self._kernel32.CloseHandle(handle):
            raise ProcessTreeTerminationError(
                "CloseHandle failed for the owned Windows Job Object"
            )


def _create_windows_job(
    pid: int,
    *,
    process_handle: Any | None = None,
) -> _WindowsJob | None:
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("per_process_user_time_limit", ctypes.c_longlong),
                ("per_job_user_time_limit", ctypes.c_longlong),
                ("limit_flags", wintypes.DWORD),
                ("minimum_working_set_size", ctypes.c_size_t),
                ("maximum_working_set_size", ctypes.c_size_t),
                ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority_class", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("read_operation_count", ctypes.c_ulonglong),
                ("write_operation_count", ctypes.c_ulonglong),
                ("other_operation_count", ctypes.c_ulonglong),
                ("read_transfer_count", ctypes.c_ulonglong),
                ("write_transfer_count", ctypes.c_ulonglong),
                ("other_transfer_count", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("basic_limit_information", BasicLimitInformation),
                ("io_info", IoCounters),
                ("process_memory_limit", ctypes.c_size_t),
                ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory_used", ctypes.c_size_t),
                ("peak_job_memory_used", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            return None
        info = ExtendedLimitInformation()
        info.basic_limit_information.limit_flags = 0x00002000
        if not kernel32.SetInformationJobObject(
            job_handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job_handle)
            return None
        owns_process_handle = process_handle is None
        if process_handle is None:
            process_handle = kernel32.OpenProcess(
                0x0001 | 0x0100 | 0x1000,
                False,
                pid,
            )
            if not process_handle:
                kernel32.CloseHandle(job_handle)
                return None
        try:
            assigned = kernel32.AssignProcessToJobObject(
                job_handle,
                process_handle,
            )
        finally:
            if owns_process_handle:
                kernel32.CloseHandle(process_handle)
        if not assigned:
            kernel32.CloseHandle(job_handle)
            return None
        return _WindowsJob(kernel32, job_handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_process_handle(process: object) -> Any | None:
    handle = getattr(process, "_handle", None)
    if handle is not None:
        return handle
    transport = getattr(process, "_transport", None)
    popen = getattr(transport, "_proc", None)
    return getattr(popen, "_handle", None)


def _resume_windows_process(process: object) -> bool:
    if not _IS_WINDOWS:
        return True
    handle = _windows_process_handle(process)
    if handle is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        return ntdll.NtResumeProcess(handle) == 0
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _terminate_bound_windows_job(process: object) -> bool:
    windows_job = getattr(process, "_demiurge_windows_job", None)
    if windows_job is None:
        return False
    try:
        windows_job.terminate()
    finally:
        try:
            delattr(process, "_demiurge_windows_job")
        except AttributeError:
            pass
    return True


def _release_bound_windows_job(process: object) -> bool:
    windows_job = getattr(process, "_demiurge_windows_job", None)
    if windows_job is None:
        return False
    try:
        close = getattr(windows_job, "close", None)
        if callable(close):
            close()
        else:
            windows_job.terminate()
    finally:
        try:
            delattr(process, "_demiurge_windows_job")
        except AttributeError:
            pass
    return True


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except (PermissionError, ProcessLookupError):
        return False
    return True


def _terminate_windows_process_tree(pid: int) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return completed.returncode == 0


class _BoundedTextTail:
    def __init__(self, limit_chars: int):
        self.limit_chars = max(1, int(limit_chars))
        self.total_chars = 0
        self.total_bytes = 0
        self._tail = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def append(self, chunk: bytes) -> str:
        self.total_bytes += len(chunk)
        text = self._decoder.decode(chunk)
        self._append_text(text)
        return text

    def finish(self) -> str:
        text = self._decoder.decode(b"", final=True)
        self._append_text(text)
        return text

    def render(self) -> str:
        if self.total_chars <= self.limit_chars:
            return self._tail
        marker = "...[truncated output; showing tail]\n"
        retained = max(0, self.limit_chars - len(marker))
        omitted = self.total_chars - retained
        marker = f"...[truncated {omitted} chars; showing tail]\n"
        retained = max(0, self.limit_chars - len(marker))
        return marker + self._tail[-retained:] if retained else marker[: self.limit_chars]

    def stats(self) -> dict[str, object]:
        return {
            "total_bytes": self.total_bytes,
            "total_chars": self.total_chars,
            "retained_chars": len(self.render()),
            "truncated": self.total_chars > self.limit_chars,
        }

    def _append_text(self, text: str) -> None:
        if not text:
            return
        self.total_chars += len(text)
        self._tail = (self._tail + text)[-self.limit_chars :]


@dataclass(slots=True)
class _ProcessDrain:
    label: str
    artifact_path: Path | None
    thread: threading.Thread
    artifact_error: BaseException | None = None

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)


def _start_drain_thread(
    pipe: object,
    tail: _BoundedTextTail,
    *,
    label: str,
    artifact_path: Path | None = None,
    redactions: Mapping[str, str] | None = None,
) -> _ProcessDrain:
    drain_state: _ProcessDrain | None = None

    def record_artifact_error(exc: BaseException) -> None:
        assert drain_state is not None
        if drain_state.artifact_error is None:
            drain_state.artifact_error = exc

    def close_sink(sink: _TextArtifactSink) -> None:
        try:
            sink.close()
        except (OSError, ValueError) as exc:
            record_artifact_error(exc)

    def drain() -> None:
        sink: _TextArtifactSink | None = None
        try:
            try:
                sink = _TextArtifactSink(
                    artifact_path,
                    redactions=redactions,
                )
            except (OSError, ValueError) as exc:
                record_artifact_error(exc)
            while True:
                try:
                    chunk = pipe.read(64 * 1024)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                text = tail.append(chunk)
                if sink is not None:
                    try:
                        sink.append(text)
                    except (OSError, ValueError) as exc:
                        record_artifact_error(exc)
                        close_sink(sink)
                        sink = None
        finally:
            final_text = tail.finish()
            if sink is not None:
                try:
                    sink.append(final_text)
                except (OSError, ValueError) as exc:
                    record_artifact_error(exc)
                close_sink(sink)

    thread = threading.Thread(target=drain, daemon=True)
    drain_state = _ProcessDrain(
        label=label,
        artifact_path=artifact_path,
        thread=thread,
    )
    thread.start()
    return drain_state


def _join_drain_threads(
    drains: list[_ProcessDrain],
    *,
    timeout_seconds: float | None = None,
) -> None:
    deadline = (
        time.monotonic() + timeout_seconds
        if timeout_seconds is not None
        else None
    )
    for drain in drains:
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        drain.join(remaining)


def _raise_output_artifact_error(drains: list[_ProcessDrain]) -> None:
    failures = {
        drain.label: drain.artifact_error
        for drain in drains
        if drain.artifact_error is not None
    }
    if failures:
        raise ProcessOutputArtifactError(failures)


def _close_pipe(pipe: object | None) -> None:
    if pipe is None:
        return
    try:
        pipe.close()
    except OSError:
        pass


class _TextArtifactSink:
    def __init__(
        self,
        path: Path | None,
        *,
        redactions: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path
        self._stream = None
        self._redactor = _StreamingTextRedactor(redactions or {})
        if path is None:
            return
        self._stream = open_private_text(
            path,
            "w",
            encoding="utf-8",
        )

    def append(self, text: str) -> None:
        if self._stream is None or not text:
            return
        rendered = self._redactor.append(text)
        if rendered:
            self._stream.write(rendered)

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        failure: BaseException | None = None
        try:
            final_text = self._redactor.finish()
            if final_text:
                stream.write(final_text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException as exc:
            failure = exc
        try:
            stream.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
        if failure is not None:
            raise failure


class _StreamingTextRedactor:
    def __init__(self, replacements: Mapping[str, str]) -> None:
        self._replacements = tuple(
            sorted(
                (
                    (value, replacement)
                    for value, replacement in replacements.items()
                    if value
                ),
                key=lambda item: len(item[0]),
                reverse=True,
            )
        )
        self._max_value_chars = max(
            (len(value) for value, _ in self._replacements),
            default=0,
        )
        self._pending = ""

    def append(self, text: str) -> str:
        combined = self._pending + text
        if not self._replacements:
            return combined
        safe_start_limit = len(combined) - self._max_value_chars + 1
        if safe_start_limit <= 0:
            self._pending = combined
            return ""
        rendered: list[str] = []
        index = 0
        while index < safe_start_limit:
            match = next(
                (
                    (value, replacement)
                    for value, replacement in self._replacements
                    if combined.startswith(value, index)
                ),
                None,
            )
            if match is None:
                rendered.append(combined[index])
                index += 1
                continue
            value, replacement = match
            rendered.append(replacement)
            index += len(value)
        self._pending = combined[index:]
        return "".join(rendered)

    def finish(self) -> str:
        pending = self._pending
        self._pending = ""
        return self._redact_all(pending)

    def _redact_all(self, text: str) -> str:
        rendered: list[str] = []
        index = 0
        while index < len(text):
            match = next(
                (
                    (value, replacement)
                    for value, replacement in self._replacements
                    if text.startswith(value, index)
                ),
                None,
            )
            if match is None:
                rendered.append(text[index])
                index += 1
                continue
            value, replacement = match
            rendered.append(replacement)
            index += len(value)
        return "".join(rendered)


def _existing_artifact_paths(paths: Mapping[str, Path]) -> dict[str, str]:
    return {
        label: str(path)
        for label, path in paths.items()
        if path.exists()
    }
