import asyncio
import os
import subprocess
import threading

import pytest

from demiurge.tools import process_lifecycle


class _FakeAsyncProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self.kill_calls = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class _FakeAsyncStream:
    def __init__(self, *chunks: bytes):
        self._chunks = list(chunks) + [b""]

    async def read(self, size: int) -> bytes:
        await asyncio.sleep(0)
        return self._chunks.pop(0)


class _FakeWindowsJob:
    def __init__(self):
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1


class _FakeKernel32:
    def __init__(self, *, terminate_result: int):
        self.terminate_result = terminate_result
        self.closed_handles = []

    def TerminateJobObject(self, handle, exit_code):
        return self.terminate_result

    def CloseHandle(self, handle):
        self.closed_handles.append(handle)
        return 1


class _FailingArtifactStream:
    def __init__(self):
        self.closed = False

    def write(self, text):
        return len(text)

    def flush(self):
        raise OSError("SENSITIVE_ARTIFACT_FAILURE")

    def fileno(self):
        return 99

    def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_windows_process_tree_adapter_uses_recursive_forced_taskkill(monkeypatch):
    process = _FakeAsyncProcess(pid=4312)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        process.returncode = 1
        return subprocess.CompletedProcess(command, 0, "SUCCESS", "")

    monkeypatch.setattr(process_lifecycle, "_IS_POSIX", False)
    monkeypatch.setattr(process_lifecycle, "_IS_WINDOWS", True)
    monkeypatch.setattr(process_lifecycle.subprocess, "run", fake_run)

    await process_lifecycle.terminate_async_process_tree(process)

    assert calls == [
        (
            ["taskkill", "/PID", "4312", "/T", "/F"],
            {
                "check": False,
                "capture_output": True,
                "text": True,
            },
        )
    ]
    assert process.kill_calls == 0


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_process_tree_adapter_rejects_reused_pid_with_different_start_identity(monkeypatch):
    process = _FakeAsyncProcess(pid=4312)
    current_identity = process_lifecycle.ProcessIdentity(
        pid=4312,
        spawn_id="proc_current",
        process_group_id=4312,
        platform="windows",
    )
    stale_identity = process_lifecycle.ProcessIdentity(
        pid=4312,
        spawn_id="proc_stale",
        process_group_id=4312,
        platform="windows",
    )
    process._demiurge_process_identity = current_identity
    calls = []

    monkeypatch.setattr(process_lifecycle, "_IS_POSIX", False)
    monkeypatch.setattr(process_lifecycle, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        process_lifecycle.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    await process_lifecycle.terminate_async_process_tree(
        process,
        identity=stale_identity,
    )

    assert calls == []
    assert process.kill_calls == 0


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_process_tree_adapter_rejects_changed_os_process_start_identity(monkeypatch):
    process = _FakeAsyncProcess(pid=4312)
    identity = process_lifecycle.ProcessIdentity(
        pid=4312,
        spawn_id="proc_current",
        process_group_id=4312,
        platform="windows",
        start_identity="windows:100",
    )
    process._demiurge_process_identity = identity
    calls = []

    monkeypatch.setattr(process_lifecycle, "_IS_POSIX", False)
    monkeypatch.setattr(process_lifecycle, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        process_lifecycle,
        "_read_process_start_identity",
        lambda pid: "windows:200",
    )
    monkeypatch.setattr(
        process_lifecycle.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    await process_lifecycle.terminate_async_process_tree(
        process,
        identity=identity,
    )

    assert calls == []
    assert process.kill_calls == 0


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_windows_job_owner_terminates_descendants_after_leader_exit(monkeypatch):
    process = _FakeAsyncProcess(pid=4312)
    process.returncode = 0
    identity = process_lifecycle.ProcessIdentity(
        pid=4312,
        spawn_id="proc_current",
        process_group_id=4312,
        platform="windows",
        start_identity="windows:100",
    )
    job = _FakeWindowsJob()
    taskkill_calls = []

    monkeypatch.setattr(process_lifecycle, "_IS_POSIX", False)
    monkeypatch.setattr(process_lifecycle, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        process_lifecycle,
        "_create_windows_job",
        lambda pid, **kwargs: job,
        raising=False,
    )
    monkeypatch.setattr(
        process_lifecycle,
        "_resume_windows_process",
        lambda process: True,
        raising=False,
    )
    monkeypatch.setattr(
        process_lifecycle,
        "_read_process_start_identity",
        lambda pid: "windows:100",
    )
    monkeypatch.setattr(
        process_lifecycle.subprocess,
        "run",
        lambda command, **kwargs: taskkill_calls.append((command, kwargs)),
    )

    process_lifecycle.bind_process_identity(process, identity)
    await process_lifecycle.terminate_async_process_tree(
        process,
        identity=identity,
    )

    assert job.terminate_calls == 1
    assert taskkill_calls == []


@pytest.mark.cross_platform
def test_process_identity_binding_requires_os_start_marker(monkeypatch):
    process = _FakeAsyncProcess(pid=4312)
    identity = process_lifecycle.ProcessIdentity(
        pid=4312,
        spawn_id="proc_current",
        process_group_id=4312,
        platform="posix",
        start_identity=None,
    )

    monkeypatch.setattr(process_lifecycle, "_IS_POSIX", True)
    monkeypatch.setattr(process_lifecycle, "_IS_WINDOWS", False)

    with pytest.raises(process_lifecycle.ProcessIdentityUnavailable):
        process_lifecycle.bind_process_identity(process, identity)


@pytest.mark.cross_platform
def test_windows_spawn_is_suspended_until_job_assignment(monkeypatch):
    monkeypatch.setattr(process_lifecycle, "_IS_POSIX", False)
    monkeypatch.setattr(process_lifecycle, "_IS_WINDOWS", True)
    monkeypatch.setattr(process_lifecycle.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x0200, raising=False)
    monkeypatch.setattr(process_lifecycle.subprocess, "CREATE_SUSPENDED", 0x0400, raising=False)

    assert process_lifecycle.process_group_spawn_kwargs() == {
        "creationflags": 0x0600,
    }


@pytest.mark.cross_platform
def test_windows_job_termination_failure_is_not_reported_as_success():
    kernel32 = _FakeKernel32(terminate_result=0)
    job = process_lifecycle._WindowsJob(kernel32, 99)

    with pytest.raises(process_lifecycle.ProcessTreeTerminationError):
        job.terminate()

    assert kernel32.closed_handles == [99]


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_process_lifecycle_owner_rejects_foreground_registration_after_shutdown():
    owner = process_lifecycle.ProcessLifecycleOwner()
    await owner.shutdown()
    task = asyncio.create_task(asyncio.sleep(0))

    with pytest.raises(process_lifecycle.ProcessLifecycleClosed):
        owner.track_foreground(
            cancel_event=threading.Event(),
            task=task,
        )

    await task


@pytest.mark.cross_platform
def test_streaming_artifact_redactor_handles_secret_across_chunks():
    redactor = process_lifecycle._StreamingTextRedactor(
        {"SYNTHETIC_SECRET": "<redacted:BOUND_SECRET>"}
    )

    rendered = "".join(
        [
            redactor.append("prefix-SYNTH"),
            redactor.append("ETIC_SEC"),
            redactor.append("RET-suffix"),
            redactor.finish(),
        ]
    )

    assert rendered == "prefix-<redacted:BOUND_SECRET>-suffix"


@pytest.mark.cross_platform
def test_foreground_artifact_write_failure_is_not_reported_as_success(monkeypatch, tmp_path):
    original_append = process_lifecycle._TextArtifactSink.append

    def fail_artifact_append(self, text):
        if self.path is not None:
            raise OSError("synthetic artifact write failure")
        return original_append(self, text)

    monkeypatch.setattr(
        process_lifecycle._TextArtifactSink,
        "append",
        fail_artifact_append,
    )

    with pytest.raises(process_lifecycle.ProcessOutputArtifactError):
        process_lifecycle.run_foreground_process(
            "printf artifact",
            cwd=tmp_path,
            env=os.environ,
            timeout_seconds=2,
            output_limit_chars=12000,
            output_artifact_paths={"stdout": tmp_path / "stdout.txt"},
        )


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_async_artifact_write_failure_drains_before_reporting_typed_error(monkeypatch, tmp_path):
    process = _FakeAsyncProcess(pid=4312)
    process.stdout = _FakeAsyncStream(b"stdout-1", b"stdout-2")
    process.stderr = _FakeAsyncStream(b"stderr-1", b"stderr-2")
    observed = {"stdout": [], "stderr": []}

    def fail_artifact_append(self, text):
        if self.path is not None:
            raise OSError("synthetic async artifact write failure")

    monkeypatch.setattr(
        process_lifecycle._TextArtifactSink,
        "append",
        fail_artifact_append,
    )

    with pytest.raises(process_lifecycle.ProcessOutputArtifactError) as caught:
        await process_lifecycle.drain_async_process_output(
            process,
            output_limit_chars=12000,
            output_artifact_paths={
                "stdout": tmp_path / "stdout.txt",
                "stderr": tmp_path / "stderr.txt",
            },
            on_chunk=lambda label, text: observed[label].append(text),
        )

    assert set(caught.value.failures) == {"stderr", "stdout"}
    assert "".join(observed["stdout"]) == "stdout-1stdout-2"
    assert "".join(observed["stderr"]) == "stderr-1stderr-2"
    assert process.returncode == 0


@pytest.mark.cross_platform
def test_process_output_artifact_error_does_not_retain_raw_exception():
    error = process_lifecycle.ProcessOutputArtifactError(
        {"stdout": OSError("SENSITIVE_ARTIFACT_FAILURE")}
    )

    assert error.failures == {"stdout": "OSError"}
    assert "SENSITIVE_ARTIFACT_FAILURE" not in str(error)
    assert "SENSITIVE_ARTIFACT_FAILURE" not in repr(error.failures)


@pytest.mark.cross_platform
def test_text_artifact_sink_close_releases_stream_after_flush_failure(tmp_path):
    sink = process_lifecycle._TextArtifactSink(None)
    stream = _FailingArtifactStream()
    sink.path = tmp_path / "artifact.txt"
    sink._stream = stream

    with pytest.raises(OSError, match="SENSITIVE_ARTIFACT_FAILURE"):
        sink.close()

    assert stream.closed is True
    assert sink._stream is None
