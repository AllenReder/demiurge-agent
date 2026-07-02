import pytest

from demiurge.core import SlotDefinition
from demiurge.runtime.slots import SlotInvocation, SlotRuntime


def _slot(tmp_path, name: str, code: str) -> SlotDefinition:
    root = tmp_path / name
    root.mkdir()
    (root / "module.py").write_text(code, encoding="utf-8")
    return SlotDefinition(
        kind="input",
        slot_id=name,
        path=root,
        relative_path=f"agent/input/{name}",
        manifest={},
        entrypoint="module:process",
        failure_policy="hard",
    )


@pytest.mark.asyncio
async def test_slot_runtime_invokes_sync_and_async_handlers(tmp_path):
    sync_slot = _slot(tmp_path, "sync_slot", "def process(ctx):\n    return ctx['value'] + 1\n")
    async_slot = _slot(
        tmp_path,
        "async_slot",
        "async def process(ctx):\n    return ctx['value'] + 2\n",
    )
    runtime = SlotRuntime()

    sync_outcome = await runtime.invoke(SlotInvocation(slot=sync_slot, context={"value": 1}))
    async_outcome = await runtime.invoke(SlotInvocation(slot=async_slot, context={"value": 1}))

    assert sync_outcome.status == "completed"
    assert sync_outcome.value == 2
    assert async_outcome.status == "completed"
    assert async_outcome.value == 3


@pytest.mark.asyncio
async def test_slot_runtime_returns_failed_outcome_with_original_exception(tmp_path):
    slot = _slot(tmp_path, "bad_slot", "def process(ctx):\n    raise RuntimeError('boom')\n")

    outcome = await SlotRuntime().invoke(SlotInvocation(slot=slot, context=None, phase="output", background=True))

    assert outcome.status == "failed"
    assert outcome.phase == "output"
    assert outcome.background is True
    assert outcome.error == "boom"
    with pytest.raises(RuntimeError, match="boom"):
        outcome.raise_for_error()
