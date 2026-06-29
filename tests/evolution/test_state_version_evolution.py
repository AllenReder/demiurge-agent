import pytest

from demiurge.app import create_app
from demiurge.evolution import EvolutionRuntime


@pytest.mark.asyncio
async def test_evolve_promotes_candidate_and_rollback_restores_previous(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    before = app.version_store.active_pointer("assistant")

    result = await app.evolution_runtime.evolve(
        target_core_id="assistant",
        goal="record a small v1 evolution note",
        source_turn_id="test",
    )

    assert result.promoted is True
    after = app.version_store.active_pointer("assistant")
    assert after.active_version != before.active_version
    assert after.previous_stable_version == before.active_version
    instructions = app.version_store.active_core_path("assistant").joinpath("agent/SOUL.md").read_text()
    assert "record a small v1 evolution note" in instructions

    rolled_back = app.version_store.rollback("assistant", reason="test rollback")
    assert rolled_back.active_version == before.active_version


def test_evolution_rejects_path_escape(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    candidate = app.version_store.create_candidate("assistant", run_id="manual")

    with pytest.raises(ValueError):
        app.evolution_runtime._apply_file_ops(
            candidate,
            [{"op": "write", "path": "../outside.txt", "content": "bad"}],
        )


@pytest.mark.asyncio
async def test_evolve_dependency_file_change_fails_before_gate(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    with pytest.raises(ValueError):
        await app.evolution_runtime.evolve(
            target_core_id="assistant",
            goal="try dependency change",
            file_ops=[{"op": "write", "path": "pyproject.toml", "content": "[project]\n"}],
        )
