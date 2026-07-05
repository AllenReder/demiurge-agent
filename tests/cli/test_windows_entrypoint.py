import subprocess
import sys

import pytest


pytestmark = pytest.mark.cross_platform


def test_module_entrypoint_help_starts_on_windows():
    result = subprocess.run(
        [sys.executable, "-m", "demiurge", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
