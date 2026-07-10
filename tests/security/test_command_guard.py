import pytest

from demiurge.security.command_guard import review_command


def test_command_guard_allows_common_safe_commands():
    assert review_command("printf hello").action == "allow"
    assert review_command("uv run pytest tests/tools/test_builtin_tools.py").action == "allow"
    assert review_command("rg needle . | head -20").action == "allow"
    assert review_command("rg rm .").action == "allow"
    assert review_command("cd ui-tui && npm run build").action == "allow"


def test_command_guard_prompts_for_dependency_and_shell_risk():
    assert review_command("npm install").rule_key == "dependency-change"
    assert review_command("printf hello > out.txt").rule_key == "shell-redirection"
    assert review_command("python <<'PY'\nprint('hello')\nPY").rule_key == "complex-shell"
    assert review_command("bash -c 'echo hello'").rule_key == "shell-eval"


def test_command_guard_blocks_hardline_commands():
    root_delete = review_command("rm -rf /")
    sudo_stdin = review_command("printf pw | sudo -S whoami")
    shutdown = review_command("systemctl reboot")

    assert root_delete.action == "block"
    assert root_delete.rule_key == "rm-critical-path"
    assert sudo_stdin.action == "block"
    assert sudo_stdin.rule_key == "sudo-stdin"
    assert shutdown.action == "block"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param('echo "$(rm -rf /)"', id="rm-root"),
        pytest.param('echo "$(shutdown now)"', id="shutdown"),
        pytest.param('echo "$(dd if=/dev/zero of=/dev/sda)"', id="raw-device"),
    ],
)
def test_sec_01_destructive_command_substitution_is_never_auto_approved(command):
    """SEC-01: destructive payloads inside command substitution must fail closed."""
    decision = review_command(command)

    assert decision.action != "allow"
    assert decision.risk in {"high", "critical"}
