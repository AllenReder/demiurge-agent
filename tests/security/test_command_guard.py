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
        pytest.param('echo "`rm -rf /`"', id="backtick-rm-root"),
        pytest.param("echo <(shutdown now)", id="process-substitution-shutdown"),
        pytest.param('env TRACE=1 command echo "$(dd if=/dev/zero of=/dev/sda)"', id="wrapped-raw-device"),
        pytest.param("bash -lc 'rm -rf /'", id="nested-shell-rm-root"),
        pytest.param("env MODE=test sh -c 'shutdown now'", id="wrapped-nested-shell-shutdown"),
        pytest.param("sudo -- rm -rf /", id="sudo-wrapper-rm-root"),
        pytest.param("exec bash -c 'shutdown now'", id="exec-shell-wrapper-shutdown"),
        pytest.param("nohup sh -c 'rm -rf /'", id="nohup-shell-wrapper-rm-root"),
        pytest.param("(rm -rf /)", id="subshell-group-rm-root"),
        pytest.param("{ rm -rf /; }", id="brace-group-rm-root"),
        pytest.param("eval 'shutdown now'", id="eval-shutdown"),
        pytest.param("env MODE=test eval 'echo $(rm -rf /)'", id="wrapped-eval-substitution-rm-root"),
        pytest.param("echo ＇$(shutdown now)＇", id="fullwidth-apostrophe-shutdown"),
        pytest.param("echo ＼$(shutdown now)", id="fullwidth-backslash-shutdown"),
    ],
)
def test_sec_01_destructive_nested_shell_payloads_are_blocked(command):
    """SEC-01: destructive payloads in executable shell contexts must fail closed."""
    decision = review_command(command)

    assert decision.action == "block"
    assert decision.risk == "critical"


@pytest.mark.parametrize(
    ("command", "rule_key"),
    [
        pytest.param('echo "$(printf nested-$(whoami))"', "command-substitution", id="nested-dollar-paren"),
        pytest.param('printf "%s\\n" "`whoami`"', "command-substitution", id="quoted-backticks"),
        pytest.param(
            'env TRACE=1 command echo "$(printf wrapped)"',
            "command-substitution",
            id="env-command-wrapper",
        ),
        pytest.param('echo "$\\\n(printf continued)"', "command-substitution", id="line-continuation"),
        pytest.param('echo "＄（printf unicode）"', "command-substitution", id="nfkc-fullwidth"),
        pytest.param(r'echo "prefix\"$(whoami)"', "command-substitution", id="escaped-double-quote"),
        pytest.param("echo <(printf process)", "process-substitution", id="process-input"),
        pytest.param("echo >(cat)", "process-substitution", id="process-output"),
        pytest.param('echo "$HOME"', "shell-expansion", id="parameter-short"),
        pytest.param('echo "${HOME}"', "shell-expansion", id="parameter-braced"),
        pytest.param('echo "$((1 + 1))"', "shell-expansion", id="arithmetic"),
        pytest.param('printf "%s\\n" "$[1 + 1]"', "shell-expansion", id="legacy-arithmetic"),
        pytest.param('echo "$(printf missing"', "command-substitution", id="unterminated-dollar-paren"),
        pytest.param('echo "`printf missing"', "command-substitution", id="unterminated-backtick"),
        pytest.param('echo "${HOME"', "shell-expansion", id="unterminated-parameter"),
    ],
)
def test_sec_01_executable_shell_expansions_require_approval(command, rule_key):
    decision = review_command(command)

    assert decision.action == "prompt"
    assert decision.risk == "high"
    assert decision.rule_key == rule_key


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("echo '$(printf literal)'", id="single-quoted-dollar-paren"),
        pytest.param(r'echo "\$(printf literal)"', id="escaped-dollar-paren"),
        pytest.param("printf '%s\\n' '`whoami`'", id="single-quoted-backticks"),
        pytest.param(r'printf "%s\n" "\`whoami\`"', id="escaped-backticks"),
        pytest.param("echo '<(printf literal)'", id="single-quoted-process-form"),
        pytest.param('echo "<(printf literal)"', id="double-quoted-process-form"),
        pytest.param("printf '$HOME'", id="single-quoted-parameter"),
        pytest.param('echo "cost $ USD"', id="bare-dollar"),
    ],
)
def test_sec_01_literal_shell_metacharacters_remain_safe(command):
    decision = review_command(command)

    assert decision.action == "allow"
    assert decision.risk == "low"
    assert decision.rule_key == "safe-command"


def test_sec_01_comment_marker_inside_a_word_does_not_hide_expansion():
    decision = review_command("echo safe#$(printf active)")

    assert decision.action == "prompt"
    assert decision.risk == "high"
    assert decision.rule_key == "command-substitution"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("sh -c 'echo $(printf nested)'", id="shell-c"),
        pytest.param("env MODE=test sh -c 'echo `whoami`'", id="env-shell-c"),
        pytest.param("command bash -lc 'echo ${HOME}'", id="command-shell-lc"),
    ],
)
def test_sec_01_nested_shell_wrappers_are_never_auto_approved(command):
    decision = review_command(command)

    assert decision.action == "prompt"
    assert decision.risk == "high"
    assert decision.rule_key == "shell-eval"


def test_sec_01_fullwidth_backslash_does_not_hide_a_real_newline():
    decision = review_command("printf SAFE ＼\nprintf SECOND_COMMAND")

    assert decision.action == "prompt"
    assert decision.risk == "high"
    assert decision.rule_key == "complex-shell"


def test_sec_01_deeply_nested_substitution_is_bounded_and_requires_approval():
    command = "echo " + "$(" * 64 + "printf nested" + ")" * 64

    decision = review_command(command)

    assert decision.action == "prompt"
    assert decision.risk == "high"
    assert decision.rule_key == "command-substitution"


def test_sec_01_crlf_after_backslash_remains_a_multiline_command():
    decision = review_command("printf SAFE \\\r\nprintf SECOND_COMMAND")

    assert decision.action == "prompt"
    assert decision.risk == "high"
    assert decision.rule_key == "complex-shell"
