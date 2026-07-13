from __future__ import annotations

import subprocess
import sys

import pytest

from baseline_support import BaselineContractFailure
from demiurge.app import create_app
from demiurge.providers import ToolCall
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.approval import StaticApprovalProvider
from demiurge.security.capabilities import CapabilityFacade


pytestmark = pytest.mark.stress

SMALL_INPUT_BYTES = 1 * 1024 * 1024
LARGE_INPUT_BYTES = 8 * 1024 * 1024


def _turn(core) -> TurnContext:
    return TurnContext(
        session_id="session_stress",
        turn_id="turn_stress",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="stress baseline"),
    )


def _principal_scope(app, core, turn):
    resolver = PrincipalScopeResolver(app.runtime_store)
    if not app.runtime_store.session_owner_exists(turn.session_id):
        issued = resolver.local_operator(
            active_session_id=turn.session_id,
            reason="bind direct stress tool session",
            allow_unowned_active=True,
        )
        app.session_runtime.create_session(
            session_id=turn.session_id,
            core_id=core.core_id,
            core_revision=core.revision,
            principal_scope=issued,
        )
    return resolver.origin_scope(session_id=turn.session_id)


async def _execute(app, core, name: str, arguments: dict):
    turn = _turn(core)
    return await app.runner.execute_call(
        ToolCall(name=name, arguments=arguments, id=f"call_{name}"),
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn),
        emit_event=app.runner.event_log.emit,
    )


def _create_sparse_text_file(path, size_bytes: int) -> None:
    with path.open("wb") as handle:
        handle.write(b"needle\n")
        handle.seek(size_bytes - 1)
        handle.write(b"x")


async def _read_file_peak(tmp_path, baseline_recorder, size_bytes: int) -> int:
    workspace = tmp_path / f"read-{size_bytes}"
    workspace.mkdir()
    target = workspace / "large.txt"
    _create_sparse_text_file(target, size_bytes)
    app = create_app(
        home=tmp_path / f"read-home-{size_bytes}",
        provider_name="fake",
        workspace=workspace,
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    try:
        with baseline_recorder.measure(
            "read_file_window",
            finding="IO-01",
            scale={"file_bytes": size_bytes, "requested_chars": 1_024},
        ) as sample:
            result = await _execute(
                app,
                core,
                "read_file",
                {"path": "large.txt", "offset": size_bytes - 1_024, "limit": 1_024},
            )
            sample.observations.update(
                {
                    "is_error": result.is_error,
                    "returned_chars": len(result.content),
                    "truncated": "truncated" in result.content,
                }
            )
            assert result.is_error is False
            assert len(result.content) == 1_024
        return int(sample.measurements["python_peak_bytes"] or 0)
    finally:
        await app.close()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="IO-01: read_file materializes the complete file before slicing",
)
async def test_io_01_read_file_window_memory_is_bounded_by_requested_window(tmp_path, baseline_recorder):
    small_peak = await _read_file_peak(tmp_path, baseline_recorder, SMALL_INPUT_BYTES)
    large_peak = await _read_file_peak(tmp_path, baseline_recorder, LARGE_INPUT_BYTES)

    with baseline_recorder.measure(
        "read_file_window_memory_trend",
        finding="IO-01",
        scale={"small_file_bytes": SMALL_INPUT_BYTES, "large_file_bytes": LARGE_INPUT_BYTES},
    ) as sample:
        sample.observations.update({"small_peak_bytes": small_peak, "large_peak_bytes": large_peak})
        sample.require(
            large_peak <= small_peak * 3,
            "read_file peak memory must be bounded by the requested window, not total file size",
        )


async def _search_file_peak(tmp_path, baseline_recorder, size_bytes: int) -> int:
    workspace = tmp_path / f"search-{size_bytes}"
    workspace.mkdir()
    target = workspace / "large.txt"
    _create_sparse_text_file(target, size_bytes)
    app = create_app(
        home=tmp_path / f"search-home-{size_bytes}",
        provider_name="fake",
        workspace=workspace,
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    try:
        with baseline_recorder.measure(
            "search_files_content_window",
            finding="IO-01",
            scale={"file_bytes": size_bytes, "query": "needle", "max_results": 1},
        ) as sample:
            result = await _execute(
                app,
                core,
                "search_files",
                {
                    "path": ".",
                    "target": "content",
                    "query": "needle",
                    "pattern": "*.txt",
                    "max_results": 1,
                },
            )
            sample.observations.update(
                {
                    "is_error": result.is_error,
                    "returned_chars": len(result.content),
                    "match_visible": "large.txt:1" in result.content,
                }
            )
            assert result.is_error is False
            assert "large.txt:1" in result.content
        return int(sample.measurements["python_peak_bytes"] or 0)
    finally:
        await app.close()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="IO-01: search_files reads each complete file before applying its scan cap",
)
async def test_io_01_search_memory_is_bounded_by_scan_window(tmp_path, baseline_recorder):
    small_peak = await _search_file_peak(tmp_path, baseline_recorder, SMALL_INPUT_BYTES)
    large_peak = await _search_file_peak(tmp_path, baseline_recorder, LARGE_INPUT_BYTES)

    with baseline_recorder.measure(
        "search_files_memory_trend",
        finding="IO-01",
        scale={"small_file_bytes": SMALL_INPUT_BYTES, "large_file_bytes": LARGE_INPUT_BYTES},
    ) as sample:
        sample.observations.update({"small_peak_bytes": small_peak, "large_peak_bytes": large_peak})
        sample.require(
            large_peak <= small_peak * 3,
            "search_files peak memory must be bounded by its scan window, not total file size",
        )


async def _terminal_peak(tmp_path, baseline_recorder, size_bytes: int) -> int:
    workspace = tmp_path / f"terminal-{size_bytes}"
    workspace.mkdir()
    app = create_app(
        home=tmp_path / f"terminal-home-{size_bytes}",
        provider_name="fake",
        workspace=workspace,
    )
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    command = subprocess.list2cmdline(
        [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {size_bytes})"]
    )
    try:
        with baseline_recorder.measure(
            "terminal_foreground_high_output",
            finding="IO-01",
            scale={"stdout_bytes": size_bytes},
        ) as sample:
            result = await _execute(
                app,
                core,
                "terminal",
                {"command": command, "cwd": ".", "timeout_seconds": 10},
            )
            sample.observations.update(
                {
                    "is_error": result.is_error,
                    "returned_chars": len(result.content),
                    "display_chars": len(result.display_output or ""),
                    "truncated": "truncated" in result.content,
                }
            )
            assert result.is_error is False
            assert "truncated" in result.content
            assert len(result.content) <= 12_100
        return int(sample.measurements["python_peak_bytes"] or 0)
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_io_01_terminal_memory_is_bounded_while_draining_high_output(tmp_path, baseline_recorder):
    small_peak = await _terminal_peak(tmp_path, baseline_recorder, SMALL_INPUT_BYTES)
    large_peak = await _terminal_peak(tmp_path, baseline_recorder, LARGE_INPUT_BYTES)

    with baseline_recorder.measure(
        "terminal_high_output_memory_trend",
        finding="IO-01",
        scale={"small_stdout_bytes": SMALL_INPUT_BYTES, "large_stdout_bytes": LARGE_INPUT_BYTES},
    ) as sample:
        sample.observations.update({"small_peak_bytes": small_peak, "large_peak_bytes": large_peak})
        sample.require(
            large_peak <= small_peak * 3,
            "terminal peak memory must be bounded while draining, not scale with total output",
        )
