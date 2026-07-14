from __future__ import annotations

import re
import shlex
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from demiurge.security.sensitive_paths import (
    CREDENTIAL_DIRECTORY_NAMES,
    CREDENTIAL_FILE_NAMES,
)


CommandGuardAction = Literal["allow", "prompt", "block"]


@dataclass(frozen=True, slots=True)
class CommandGuardDecision:
    action: CommandGuardAction
    risk: str
    reason: str
    rule_key: str


@dataclass(frozen=True, slots=True)
class _ShellSplit:
    segments: tuple[str, ...]
    separators: tuple[str, ...]
    unsupported: str | None = None
    has_redirection: bool = False


@dataclass(frozen=True, slots=True)
class _ShellExpansionScan:
    reason: str | None = None
    rule_key: str | None = None
    hardline: tuple[str, str] | None = None
    filename_expansion: bool = False


_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SAFE_SCRIPT_RE = re.compile(r"^(test|tests|build|lint|check|typecheck|dev|preview)(:.+)?$")
_SHELL_EXPANSION_PRIORITY = {
    "shell-expansion": 1,
    "process-substitution": 2,
    "command-substitution": 3,
}
_MAX_SHELL_EXPANSION_DEPTH = 32
_COMMAND_GUARD_ACTION_PRIORITY = {"allow": 0, "prompt": 1, "block": 2}
_COMMAND_GUARD_RISK_PRIORITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_SED_BASIC_ADDRESS_RE = r"(?:\d+(?:~\d+)?|\$|/(?:\\.|[^/\n])*/)"
_SED_RANGE_END_ADDRESS_RE = rf"(?:{_SED_BASIC_ADDRESS_RE}|[+~]\d+)"

_PROMPT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), key, reason)
    for pattern, key, reason in [
        (r"(?:^|[\s/])\.env(?:[.\s/]|$)", "sensitive-path", "touches environment files"),
        (r"(?:^|[\s/])config\.yaml(?:[\s/]|$)", "sensitive-path", "touches config.yaml"),
    ]
)


def review_command(command: str) -> CommandGuardDecision:
    if "\x00" in command:
        return CommandGuardDecision("prompt", "high", "null bytes are not supported in shell commands", "complex-shell")
    candidates = _detection_candidates(command)
    if not candidates:
        return CommandGuardDecision("prompt", "high", "empty command", "empty-command")
    decisions = [_review_detection_candidate(candidate) for candidate in candidates]
    return max(
        decisions,
        key=lambda decision: (
            _COMMAND_GUARD_ACTION_PRIORITY[decision.action],
            _COMMAND_GUARD_RISK_PRIORITY.get(decision.risk, 0),
        ),
    )


def _review_detection_candidate(command: str) -> CommandGuardDecision:
    if not command:
        return CommandGuardDecision("prompt", "high", "empty command", "empty-command")

    expansion = _scan_shell_expansions(command)
    if expansion.hardline is not None:
        reason, key = expansion.hardline
        return CommandGuardDecision("block", "critical", reason, key)

    split = _split_shell(command)
    hardline = _detect_hardline(command, split)
    if hardline is not None:
        reason, key = hardline
        return CommandGuardDecision("block", "critical", reason, key)

    if expansion.rule_key is not None:
        return CommandGuardDecision(
            "prompt",
            "high",
            expansion.reason or "shell expansion is not auto-approved",
            expansion.rule_key,
        )
    if expansion.filename_expansion:
        return CommandGuardDecision(
            "prompt",
            "high",
            "unquoted filename expansion is not auto-approved",
            "filename-expansion",
        )

    if split.unsupported is not None:
        return CommandGuardDecision("prompt", "high", split.unsupported, "complex-shell")
    if split.has_redirection:
        return CommandGuardDecision("prompt", "high", "shell redirection can write or read arbitrary paths", "shell-redirection")

    parsed: list[list[str]] = []
    for segment in split.segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return CommandGuardDecision("prompt", "high", "command has unsupported quoting", "complex-shell")
        if not tokens:
            return CommandGuardDecision("prompt", "high", "empty shell segment", "complex-shell")
        parsed.append(tokens)

    token_prompt = _detect_promptable_tokens(parsed)
    if token_prompt is not None:
        reason, key = token_prompt
        return CommandGuardDecision("prompt", "high", reason, key)

    prompt = _detect_promptable(command)
    if prompt is not None:
        reason, key = prompt
        return CommandGuardDecision("prompt", "high", reason, key)

    if "|" in split.separators and not all(_is_readonly_safe(tokens) for tokens in parsed):
        return CommandGuardDecision("prompt", "high", "pipeline is only auto-approved for read-only commands", "pipeline")

    for tokens in parsed:
        reason = _unsafe_path_reason(tokens)
        if reason is not None:
            return CommandGuardDecision("prompt", "high", reason, "path-outside-workspace")
        safe_kind = _safe_kind(tokens)
        if safe_kind == "dev":
            return CommandGuardDecision(
                "prompt",
                "high",
                "executes workspace or project code",
                "project-code-execution",
            )
        if safe_kind is None:
            return CommandGuardDecision("prompt", "high", f"unrecognized terminal command: {_command_name(tokens)}", "unknown-command")

    return CommandGuardDecision("allow", "low", "safe terminal command", "safe-command")


def _detection_candidates(command: str) -> tuple[str, ...]:
    raw = command.strip()
    if not raw:
        return ()
    execution_faithful = _collapse_line_continuations(raw)
    ansi_stripped = _collapse_line_continuations(_ANSI_RE.sub("", raw))
    confusable_folded = unicodedata.normalize("NFKC", ansi_stripped)
    candidates: list[str] = []
    for candidate in (execution_faithful, ansi_stripped, confusable_folded):
        candidate = candidate.strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _collapse_line_continuations(command: str) -> str:
    return command.replace("\\\n", "")


def _scan_shell_expansions(command: str, *, _depth: int = 0) -> _ShellExpansionScan:
    issue_reason: str | None = None
    issue_key: str | None = None
    hardline: tuple[str, str] | None = None
    filename_expansion = False
    quote: str | None = None
    index = 0

    def record_issue(reason: str, rule_key: str) -> None:
        nonlocal issue_reason, issue_key
        current_priority = _SHELL_EXPANSION_PRIORITY.get(issue_key or "", 0)
        if _SHELL_EXPANSION_PRIORITY[rule_key] > current_priority:
            issue_reason = reason
            issue_key = rule_key

    while index < len(command):
        char = command[index]

        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue

        if char == "\\":
            index += 2
            continue

        if quote is None and char == "'":
            quote = "'"
            index += 1
            continue
        if char == '"':
            quote = None if quote == '"' else '"'
            index += 1
            continue

        if quote is None and char in "*?[{":
            filename_expansion = True

        if quote is None and command.startswith("<(", index):
            end = _scan_parenthesized_end(command, index)
            payload_end = end - 1 if end is not None else len(command)
            nested_hardline = _hardline_in_expansion(command[index + 2 : payload_end], depth=_depth)
            hardline = hardline or nested_hardline
            record_issue("process substitution is not auto-approved", "process-substitution")
            index = end if end is not None else len(command)
            continue
        if quote is None and command.startswith(">(", index):
            end = _scan_parenthesized_end(command, index)
            payload_end = end - 1 if end is not None else len(command)
            nested_hardline = _hardline_in_expansion(command[index + 2 : payload_end], depth=_depth)
            hardline = hardline or nested_hardline
            record_issue("process substitution is not auto-approved", "process-substitution")
            index = end if end is not None else len(command)
            continue

        if char == "`":
            end = _scan_backtick_end(command, index)
            payload_end = end - 1 if end is not None else len(command)
            nested_hardline = _hardline_in_expansion(command[index + 1 : payload_end], depth=_depth)
            hardline = hardline or nested_hardline
            record_issue("command substitution is not auto-approved", "command-substitution")
            index = end if end is not None else len(command)
            continue

        if char == "$":
            if command.startswith("$(", index) and not command.startswith("$((", index):
                end = _scan_parenthesized_end(command, index)
                payload_end = end - 1 if end is not None else len(command)
                nested_hardline = _hardline_in_expansion(command[index + 2 : payload_end], depth=_depth)
                hardline = hardline or nested_hardline
                record_issue("command substitution is not auto-approved", "command-substitution")
                index = end if end is not None else len(command)
                continue
            if command.startswith("$((", index):
                record_issue("shell expansion is not auto-approved", "shell-expansion")
                index += 3
                continue
            if command.startswith("$[", index):
                record_issue("shell expansion is not auto-approved", "shell-expansion")
                index += 2
                continue
            if command.startswith("${", index) or _starts_shell_parameter(command, index):
                record_issue("shell expansion is not auto-approved", "shell-expansion")

        index += 1

    return _ShellExpansionScan(
        reason=issue_reason,
        rule_key=issue_key,
        hardline=hardline,
        filename_expansion=filename_expansion,
    )


def _starts_shell_parameter(command: str, index: int) -> bool:
    if index + 1 >= len(command):
        return False
    following = command[index + 1]
    return following.isalnum() or following == "_" or following in "*@#?-$!\"'"


def _scan_parenthesized_end(command: str, start: int) -> int | None:
    depth = 1
    quote: str | None = None
    index = start + 2
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if quote is None and char == "'":
            quote = "'"
            index += 1
            continue
        if char == '"':
            quote = None if quote == '"' else '"'
            index += 1
            continue
        if quote == '"':
            if char == "`":
                backtick_end = _scan_backtick_end(command, index)
                index = backtick_end if backtick_end is not None else len(command)
                continue
            if command.startswith("$((", index):
                depth += 2
                index += 3
                continue
            if command.startswith("$(", index):
                depth += 1
                index += 2
                continue
            index += 1
            continue
        if char == "`":
            backtick_end = _scan_backtick_end(command, index)
            index = backtick_end if backtick_end is not None else len(command)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _scan_backtick_end(command: str, start: int) -> int | None:
    index = start + 1
    while index < len(command):
        if command[index] == "\\":
            index += 2
            continue
        if command[index] == "`":
            return index + 1
        index += 1
    return None


def _hardline_in_expansion(payload: str, *, depth: int) -> tuple[str, str] | None:
    if depth < _MAX_SHELL_EXPANSION_DEPTH:
        nested = _scan_shell_expansions(payload, _depth=depth + 1)
        if nested.hardline is not None:
            return nested.hardline
    split = _split_shell(payload)
    return _detect_hardline(payload, split, _depth=depth + 1)


def _split_shell(command: str) -> _ShellSplit:
    segments: list[str] = []
    separators: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    has_redirection = False
    i = 0
    while i < len(command):
        char = command[i]
        if escaped:
            current.append(char)
            escaped = False
            i += 1
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            i += 1
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            i += 1
            continue
        if char == "`" or command.startswith("$(", i):
            return _ShellSplit((), (), "command substitution is not auto-approved")
        if command.startswith("<<", i):
            return _ShellSplit((), (), "heredoc is not auto-approved")
        if command.startswith("||", i):
            return _ShellSplit((), (), "conditional OR is not auto-approved")
        if char in {"\n", "\r"}:
            return _ShellSplit((), (), "multi-line shell commands are not auto-approved")
        if char in {">", "<"}:
            has_redirection = True
        if command.startswith("&&", i):
            segments.append("".join(current).strip())
            separators.append("&&")
            current = []
            i += 2
            continue
        if char == ";":
            segments.append("".join(current).strip())
            separators.append(";")
            current = []
            i += 1
            continue
        if char == "|":
            segments.append("".join(current).strip())
            separators.append("|")
            current = []
            i += 1
            continue
        if char == "&":
            return _ShellSplit((), (), "background shell operator is not auto-approved")
        current.append(char)
        i += 1
    if quote is not None:
        return _ShellSplit((), (), "unterminated shell quote")
    segments.append("".join(current).strip())
    return _ShellSplit(tuple(segments), tuple(separators), has_redirection=has_redirection)


def _detect_hardline(command: str, split: _ShellSplit, *, _depth: int = 0) -> tuple[str, str] | None:
    lowered = command.lower()
    if re.search(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", lowered):
        return ("fork bomb", "fork-bomb")
    if re.search(r">\s*/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*\b", lowered):
        return ("write to raw block device", "raw-block-device")

    segments = split.segments
    if not segments:
        segments = (command,)
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if _contains_sudo_stdin(tokens):
            return ("sudo password guessing via stdin", "sudo-stdin")
        tokens = _normalize_hardline_tokens(tokens)
        index, command_name = _hardline_effective_command(tokens)
        if not command_name:
            continue
        args = tokens[index + 1 :]
        if _depth < _MAX_SHELL_EXPANSION_DEPTH:
            script = None
            if command_name in {"bash", "sh", "zsh", "ksh"}:
                script = _shell_eval_script(args)
            elif command_name == "eval" and args:
                script = " ".join(args)
            if script is not None:
                nested_expansion = _scan_shell_expansions(script, _depth=_depth + 1)
                if nested_expansion.hardline is not None:
                    return nested_expansion.hardline
                nested_hardline = _detect_hardline(script, _split_shell(script), _depth=_depth + 1)
                if nested_hardline is not None:
                    return nested_hardline
        if command_name == "rm" and _rm_targets_critical(args):
            return ("recursive delete of root, home, or system directory", "rm-critical-path")
        if command_name.startswith("mkfs"):
            return ("format filesystem", "mkfs")
        if command_name == "dd" and any(re.match(r"of=/dev/(sd|nvme|hd|mmcblk|vd|xvd)", arg) for arg in args):
            return ("dd writes to raw block device", "raw-block-device")
        if command_name in {"shutdown", "reboot", "halt", "poweroff"}:
            return ("system shutdown or reboot", "shutdown")
        if command_name == "init" and any(arg in {"0", "6"} for arg in args):
            return ("init shutdown or reboot", "shutdown")
        if command_name == "telinit" and any(arg in {"0", "6"} for arg in args):
            return ("telinit shutdown or reboot", "shutdown")
        if command_name == "systemctl" and any(arg in {"poweroff", "reboot", "halt", "kexec"} for arg in args):
            return ("systemctl shutdown or reboot", "shutdown")
        if command_name == "kill" and "-1" in args:
            return ("kill all processes", "kill-all")
    return None


def _normalize_hardline_tokens(tokens: list[str]) -> list[str]:
    normalized = list(tokens)
    while normalized and normalized[0] in {"(", "{", "!"}:
        normalized.pop(0)
    if normalized:
        normalized[0] = normalized[0].lstrip("(")
        if not normalized[0]:
            normalized.pop(0)
    while normalized and normalized[-1] in {")", "}"}:
        normalized.pop()
    if normalized:
        normalized[-1] = normalized[-1].rstrip(")}")
        if not normalized[-1]:
            normalized.pop()
    return normalized


def _hardline_effective_command(tokens: list[str]) -> tuple[int, str]:
    index = 0
    while index < len(tokens) and _ASSIGNMENT_RE.match(tokens[index]):
        index += 1
    while index < len(tokens):
        name = PurePosixPath(tokens[index]).name
        if name not in {"command", "env", "exec", "nice", "nohup", "setsid", "sudo", "time"}:
            return index, name
        wrapper = name
        wrapper_index = index
        index += 1
        if wrapper == "command" and any(arg in {"-v", "-V"} for arg in tokens[index:] if arg.startswith("-")):
            return wrapper_index, wrapper
        options_with_value: set[str] = set()
        allow_plus = False
        if wrapper == "env":
            options_with_value = {"-a", "--argv0", "-C", "--chdir", "-S", "--split-string", "-u", "--unset"}
        elif wrapper == "exec":
            options_with_value = {"-a"}
        elif wrapper == "nice":
            options_with_value = {"-n", "--adjustment"}
        elif wrapper == "sudo":
            options_with_value = {
                "-C",
                "--close-from",
                "-D",
                "--chdir",
                "-g",
                "--group",
                "-h",
                "--host",
                "-p",
                "--prompt",
                "-R",
                "--chroot",
                "-r",
                "--role",
                "-T",
                "--command-timeout",
                "-t",
                "--type",
                "-u",
                "--user",
            }
        elif wrapper == "time":
            options_with_value = {"-f", "--format", "-o", "--output"}
            allow_plus = True
        index = _skip_wrapper_options(tokens, index, options_with_value=options_with_value, allow_plus=allow_plus)
        while index < len(tokens) and _ASSIGNMENT_RE.match(tokens[index]):
            index += 1
    return index, ""


def _skip_wrapper_options(
    tokens: list[str],
    index: int,
    *,
    options_with_value: set[str],
    allow_plus: bool,
) -> int:
    while index < len(tokens):
        arg = tokens[index]
        if arg == "--":
            return index + 1
        is_option = arg.startswith("-") or (allow_plus and arg.startswith("+"))
        if not is_option or arg in {"-", "+"}:
            return index
        index += 2 if arg in options_with_value else 1
    return index


def _shell_eval_script(args: list[str]) -> str | None:
    index = 0
    options_with_value = {"-O", "+O", "--init-file", "--rcfile"}
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return None
        if arg in options_with_value:
            index += 2
            continue
        if not arg.startswith(("-", "+")) or arg in {"-", "+"}:
            return None
        if not arg.startswith("--") and "c" in arg[1:]:
            return args[index + 1] if index + 1 < len(args) else ""
        index += 1
    return None


def _contains_sudo_stdin(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if PurePosixPath(token).name != "sudo":
            continue
        for arg in tokens[index + 1 :]:
            if arg == "--":
                return False
            if arg == "-S" or arg == "--stdin" or (arg.startswith("-") and "S" in arg[1:]):
                return True
    return False


def _rm_targets_critical(args: list[str]) -> bool:
    recursive = any(arg.startswith("-") and ("r" in arg.lower() or "R" in arg) for arg in args)
    if not recursive:
        return False
    targets = [arg for arg in args if not arg.startswith("-")]
    critical = {"/", "/*", "/home", "/home/*", "/root", "/root/*", "/etc", "/etc/*", "/usr", "/usr/*", "/var", "/var/*", "/bin", "/bin/*", "/sbin", "/sbin/*", "/boot", "/boot/*", "/lib", "/lib/*", "~", "~/", "~/*", "$HOME", "$HOME/"}
    return any(target.rstrip("/") in critical or target in critical for target in targets)


def _detect_promptable(command: str) -> tuple[str, str] | None:
    for pattern, key, reason in _PROMPT_PATTERNS:
        if pattern.search(command):
            return reason, key
    return None


def _detect_promptable_tokens(commands: list[list[str]]) -> tuple[str, str] | None:
    for tokens in commands:
        wrapper_prompt = _promptable_env_wrapper(tokens)
        if wrapper_prompt is not None:
            return wrapper_prompt
        index, name = _effective_command(tokens)
        if not name:
            continue
        args = tokens[index + 1 :]
        if not _is_bare_executable(tokens[index]):
            return (
                "relative or explicit executable paths can run workspace code",
                "project-code-execution",
            )
        has_inline_env_overlay = any(
            _ASSIGNMENT_RE.match(token)
            for token in tokens[:index]
        )
        if name == "cd":
            return (
                "shell working-directory changes require approval",
                "cwd-change",
            )
        if name in {"rm", "rmdir", "unlink"}:
            return ("delete files from the terminal", "file-delete")
        if name in {"cp", "mv", "mkdir", "touch", "chmod", "chown", "chgrp", "ln", "install"}:
            return ("write or mutate files", "file-write")
        if name == "tee":
            return ("write files through tee", "file-write")
        if name == "sort" and any(
            arg == "--compress-program"
            or arg.startswith("--compress-program=")
            for arg in args
        ):
            return (
                "sort executes a compression program",
                "project-code-execution",
            )
        if name == "sort" and _has_option(
            args,
            short="-T",
            long="--temporary-directory",
        ):
            return ("sort writes temporary files", "file-write")
        if name in {"sort", "tree"} and _has_option(
            args,
            short="-o",
            long="--output",
        ):
            return (f"{name} writes output to a file", "file-write")
        if name == "uniq" and _uniq_writes_file(args):
            return ("uniq writes output to a file", "file-write")
        if name == "file" and (
            "--compile" in args or _has_short_flag(args, "C")
        ):
            return ("file compiles a magic database", "file-write")
        if name == "date" and any(
            arg == "-s"
            or (arg.startswith("-s") and len(arg) > 2)
            or arg == "--set"
            or arg.startswith("--set=")
            for arg in args
        ):
            return ("date changes the system clock", "system-time")
        if name == "sed" and _has_option(
            args,
            short="-f",
            long="--file",
        ):
            return (
                "sed executes commands loaded from a script file",
                "project-code-execution",
            )
        path_prompt = _embedded_path_option_prompt(name, args)
        if path_prompt is not None:
            return path_prompt
        if name == "find" and _find_deletes(args):
            return ("find deleting files", "find-delete")
        if name == "find" and any(
            arg in {"-fls", "-fprint", "-fprint0", "-fprintf"}
            for arg in args
        ):
            return ("find writes output to a file", "file-write")
        if name == "find" and any(
            arg in {"-exec", "-execdir", "-ok", "-okdir"}
            for arg in args
        ):
            return (
                "find executes workspace or project code",
                "project-code-execution",
            )
        if name == "rg" and any(
            arg == "--pre" or arg.startswith("--pre=")
            for arg in args
        ):
            return (
                "ripgrep executes a preprocessor command",
                "project-code-execution",
            )
        if name == "git" and any(
            arg in {
                "--ext-diff",
                "--textconv",
                "--open-files-in-pager",
                "--show-signature",
            }
            or arg.startswith("--open-files-in-pager=")
            for arg in args
        ):
            return (
                "git executes a configured external command",
                "project-code-execution",
            )
        if name == "git":
            prompt = _promptable_git(args)
            if prompt is not None:
                return prompt
        if name in {"curl", "wget"}:
            return ("download from the network", "network-download")
        if name in {"bash", "sh", "zsh", "ksh"} and _has_short_flag(args, "c"):
            return ("shell command evaluation", "shell-eval")
        if name == "eval":
            return ("shell command evaluation", "shell-eval")
        if name.startswith("python"):
            if _has_short_flag(args, "c"):
                return ("python command evaluation", "script-eval")
            if args[:3] == ["-m", "pip", "install"]:
                return ("install Python packages", "dependency-change")
        if name in {"node", "perl", "ruby"} and _has_short_flag(args, "e", "c"):
            return ("script command evaluation", "script-eval")
        if name in {"sed", "perl", "ruby"} and _has_short_flag(args, "i"):
            return ("in-place file edit", "in-place-edit")
        if name == "sed" and _sed_writes_file(args):
            return ("sed writes output to a file", "file-write")
        if name == "sed" and _sed_reads_file(args):
            return ("sed reads a file from its command script", "file-read")
        if name == "sed" and _sed_executes_command(args):
            return (
                "sed executes workspace or project code",
                "project-code-execution",
            )
        if name == "sudo":
            return ("sudo command", "sudo")
        if name in {"systemctl", "service", "launchctl"}:
            return ("system service control", "service-control")
        if name in {"kill", "pkill", "killall"}:
            return ("kill processes", "process-kill")
        if name == "docker":
            return ("container lifecycle command", "container-lifecycle")
        if name in {"pip", "pip3"} and args[:1] == ["install"]:
            return ("install Python packages", "dependency-change")
        if name == "uv" and _uv_changes_dependencies(args):
            return ("change or install dependencies", "dependency-change")
        if name in {"npm", "pnpm", "yarn"} and _node_package_changes_dependencies(args):
            return ("change or install dependencies", "dependency-change")
        if name in {"npx"} or (name in {"pnpm", "yarn"} and args[:1] == ["dlx"]):
            return ("download and execute package binaries", "dependency-exec")
        if name == "cargo" and args[:1] == ["install"]:
            return ("install Rust packages", "dependency-change")
        if name == "go" and args[:1] == ["install"]:
            return ("install Go packages", "dependency-change")
        if has_inline_env_overlay:
            return (
                "inline environment assignments require approval",
                "environment-overlay",
            )
        if any(_is_sensitive_token(arg) for arg in tokens):
            return ("touches sensitive path", "sensitive-path")
    return None


def _find_deletes(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        if arg == "-delete":
            return True
        if arg in {"-exec", "-execdir"} and index + 1 < len(args):
            if PurePosixPath(args[index + 1]).name == "rm":
                return True
    return False


def _promptable_env_wrapper(tokens: list[str]) -> tuple[str, str] | None:
    command_index, _name = _effective_command(tokens)
    prefix = tokens[:command_index]
    if not any(PurePosixPath(token).name == "env" for token in prefix):
        return None
    if _has_option(prefix, short="-C", long="--chdir"):
        return (
            "env working-directory overrides require approval",
            "cwd-change",
        )
    if _has_option(prefix, short="-S", long="--split-string"):
        return ("env split-string evaluates a command", "shell-eval")
    return None


def _is_bare_executable(token: str) -> bool:
    return (
        token not in {"", ".", ".."}
        and "/" not in token
        and "\\" not in token
    )


def _has_option(
    args: list[str],
    *,
    short: str,
    long: str,
) -> bool:
    return any(
        arg in {short, long}
        or (arg.startswith(short) and len(arg) > len(short))
        or arg.startswith(f"{long}=")
        for arg in args
    )


def _option_values(
    args: list[str],
    *,
    short_options: tuple[str, ...] = (),
    long_options: tuple[str, ...] = (),
) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg in {*short_options, *long_options}:
            if index + 1 < len(args):
                values.append(args[index + 1])
            index += 2
            continue
        matched = False
        for option in short_options:
            if arg.startswith(option) and len(arg) > len(option):
                values.append(arg[len(option) :])
                matched = True
                break
        if not matched:
            for option in long_options:
                prefix = f"{option}="
                if arg.startswith(prefix):
                    values.append(arg[len(prefix) :])
                    matched = True
                    break
        index += 1
    return values


def _embedded_path_option_prompt(
    name: str,
    args: list[str],
) -> tuple[str, str] | None:
    option_args = args
    short_options: tuple[str, ...] = ()
    long_options: tuple[str, ...] = ()
    if name in {"rg", "grep"}:
        short_options = ("-f",)
        long_options = ("--file", "--exclude-from")
    elif name == "file":
        short_options = ("-f", "-m")
        long_options = ("--files-from", "--magic-file")
    elif name == "date":
        short_options = ("-f", "-r")
        long_options = ("--file", "--reference")
    elif name == "du":
        short_options = ("-X",)
        long_options = ("--exclude-from",)
    elif name == "sort":
        long_options = ("--random-source",)
    elif name == "wc":
        long_options = ("--files0-from",)
    elif name == "git" and args:
        option_args = args[1:]
        if args[0] == "grep":
            short_options = ("-f",)
            long_options = ("--file",)
        elif args[0] in {"diff", "log", "show"}:
            short_options = ("-O",)
        elif args[0] == "ls-files":
            short_options = ("-X",)
            long_options = ("--exclude-from", "--pathspec-from-file")
    for value in _option_values(
        option_args,
        short_options=short_options,
        long_options=long_options,
    ):
        if _is_sensitive_token(value):
            return ("option reads a sensitive path", "sensitive-path")
        unsafe_reason = _unsafe_path_reason([value])
        if unsafe_reason is not None:
            return (unsafe_reason, "path-outside-workspace")
    return None


def _uniq_writes_file(args: list[str]) -> bool:
    options_with_value = {
        "-f",
        "--skip-fields",
        "-s",
        "--skip-chars",
        "-w",
        "--check-chars",
    }
    positionals: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            positionals.extend(args[index + 1 :])
            break
        if arg in options_with_value:
            index += 2
            continue
        if arg.startswith(("--skip-fields=", "--skip-chars=", "--check-chars=")):
            index += 1
            continue
        if re.match(r"^-[fsw].+", arg):
            index += 1
            continue
        if arg.startswith("-") and arg != "-":
            index += 1
            continue
        positionals.append(arg)
        index += 1
    return len(positionals) >= 2 and positionals[1] != "-"


def _sed_scripts(args: list[str]) -> list[str]:
    scripts: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-e", "--expression"}:
            if index + 1 < len(args):
                scripts.append(args[index + 1])
            index += 2
            continue
        if arg.startswith("--expression="):
            scripts.append(arg.split("=", 1)[1])
            index += 1
            continue
        if arg.startswith("-e") and len(arg) > 2:
            scripts.append(arg[2:])
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        if not scripts:
            scripts.append(arg)
        break
    return scripts


def _sed_writes_file(args: list[str]) -> bool:
    return _sed_script_has_effect(
        args,
        command_letters="wW",
        substitution_flag="w",
    )


def _sed_reads_file(args: list[str]) -> bool:
    return _sed_script_has_effect(
        args,
        command_letters="rR",
        substitution_flag=None,
    )


def _sed_executes_command(args: list[str]) -> bool:
    return _sed_script_has_effect(
        args,
        command_letters="e",
        substitution_flag="e",
    )


def _sed_script_has_effect(
    args: list[str],
    *,
    command_letters: str,
    substitution_flag: str | None,
) -> bool:
    scripts = _sed_scripts(args)
    addressed_command = re.compile(
        r"(?:^|[;\n{}])\s*"
        rf"(?:{_SED_BASIC_ADDRESS_RE}"
        rf"(?:\s*,\s*{_SED_RANGE_END_ADDRESS_RE})?\s*)?"
        rf"(?:!\s*)?[{re.escape(command_letters)}](?:\s|$)"
    )
    substitution_effect = (
        re.compile(
            r"(?:^|[;\n])\s*s(.).*\1.*\1[^;\s]*"
            rf"{re.escape(substitution_flag)}(?:[;\s]|$)"
        )
        if substitution_flag is not None
        else None
    )
    return any(
        addressed_command.search(script)
        or (
            substitution_effect is not None
            and substitution_effect.search(script)
        )
        for script in scripts
    )


def _promptable_git(args: list[str]) -> tuple[str, str] | None:
    if not args:
        return None
    command = args[0]
    if command == "reset" and "--hard" in args[1:]:
        return ("git reset --hard destroys worktree changes", "git-destructive")
    if command == "clean" and any(arg.startswith("-") and "f" in arg for arg in args[1:]):
        return ("git clean force deletes untracked files", "git-destructive")
    if command == "push" and any(arg == "--force" or arg == "-f" or arg.startswith("--force-") for arg in args[1:]):
        return ("git force push rewrites remote history", "git-force-push")
    if command == "branch" and "-D" in args[1:]:
        return ("git branch -D force deletes a branch", "git-destructive")
    if any(
        arg == "--output" or arg.startswith("--output=")
        for arg in args[1:]
    ):
        return ("git command writes output to a file", "file-write")
    if command in {"clone", "fetch", "pull", "push", "ls-remote"}:
        return ("git command accesses a remote repository", "network-command")
    if command == "remote" and args[1:2] in (["update"], ["prune"], ["show"]):
        return ("git remote command accesses a remote repository", "network-command")
    if command in {"remote", "branch", "tag", "worktree"} and not _is_safe_git(args):
        return ("git command mutates repository state", "repository-mutation")
    return None


def _has_short_flag(args: list[str], *letters: str) -> bool:
    wanted = set(letters)
    for arg in args:
        if not arg.startswith("-") or arg == "--":
            continue
        if arg.startswith("--"):
            if "i" in wanted and arg == "--in-place":
                return True
            continue
        if any(letter in arg[1:] for letter in wanted):
            return True
    return False


def _uv_changes_dependencies(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] in {"add", "remove", "sync", "lock"}:
        return True
    return args[:2] in (["pip", "install"], ["tool", "install"])


def _node_package_changes_dependencies(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] in {"install", "i", "ci", "add", "update", "upgrade", "remove"}:
        return True
    return args[:2] == ["audit", "fix"]


def _is_sensitive_token(token: str) -> bool:
    candidate = _path_candidate_from_token(token)
    if candidate is None:
        return False
    parts = {part.lower() for part in PurePosixPath(candidate).parts}
    if any(part.startswith(".env") for part in parts) or "config.yaml" in parts:
        return True
    if parts.intersection(CREDENTIAL_DIRECTORY_NAMES):
        return True
    name = PurePosixPath(candidate).name.lower()
    return name in CREDENTIAL_FILE_NAMES


def _unsafe_path_reason(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in {"|", "&&", ";"}:
            continue
        candidate = _path_candidate_from_token(token)
        if candidate is None:
            continue
        if candidate.startswith(("/", "~")):
            return "absolute or home-relative paths are not auto-approved in terminal commands"
        parts = PurePosixPath(candidate).parts
        if ".." in parts:
            return "parent-directory paths are not auto-approved in terminal commands"
    return None


def _path_candidate_from_token(token: str) -> str | None:
    if not token.startswith("-"):
        return token
    if "=" not in token:
        return None
    return token.split("=", 1)[1]


def _is_safe_command(tokens: list[str]) -> bool:
    return _safe_kind(tokens) is not None


def _is_readonly_safe(tokens: list[str]) -> bool:
    return _safe_kind(tokens) == "read"


def _safe_kind(tokens: list[str]) -> str | None:
    index, name = _effective_command(tokens)
    if not name:
        return None
    args = tokens[index + 1 :]
    if name in {"pwd", "ls", "cat", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "nl", "stat", "file", "du", "df", "tree", "date", "whoami", "uname", "printf", "echo", "sleep", "true", "false"}:
        return "read"
    if name in {"rg", "grep", "find"}:
        return "read"
    if name == "sed" and any(arg.startswith("-") and "n" in arg for arg in args):
        return "read"
    if name == "cd":
        return "state" if len(args) == 1 and _safe_relative_path(args[0]) else None
    if name == "git":
        return "read" if _is_safe_git(args) else None
    if name in {"pytest", "ruff", "mypy", "pyright"}:
        return "dev"
    if name.startswith("python"):
        return "dev" if _is_safe_python(args) else None
    if name == "uv":
        return "dev" if _is_safe_uv(args) else None
    if name in {"npm", "pnpm", "yarn"}:
        return "dev" if _is_safe_node_package_command(args) else None
    if name == "go":
        return "dev" if args[:1] in (["test"], ["vet"]) or args[:2] == ["test", "./..."] else None
    if name == "cargo":
        return "dev" if args[:1] in (["test"], ["check"], ["build"], ["clippy"]) or args[:2] == ["fmt", "--check"] else None
    if name == "make":
        return "dev" if args and all(_SAFE_SCRIPT_RE.match(arg) for arg in args if not arg.startswith("-")) else None
    return None


def _effective_command(tokens: list[str]) -> tuple[int, str]:
    index = 0
    while index < len(tokens) and _ASSIGNMENT_RE.match(tokens[index]):
        index += 1
    while index < len(tokens) and PurePosixPath(tokens[index]).name in {"env", "time", "command"}:
        index += 1
        while index < len(tokens) and (tokens[index].startswith("-") or _ASSIGNMENT_RE.match(tokens[index])):
            index += 1
    if index >= len(tokens):
        return index, ""
    return index, PurePosixPath(tokens[index]).name


def _command_name(tokens: list[str]) -> str:
    _, name = _effective_command(tokens)
    return name or "<empty>"


def _safe_relative_path(value: str) -> bool:
    if not value or value.startswith(("/", "~", "-")):
        return False
    return ".." not in PurePosixPath(value).parts


def _is_safe_git(args: list[str]) -> bool:
    if not args:
        return False
    command = args[0]
    if command in {"status", "diff", "log", "show", "rev-parse", "ls-files", "grep", "describe"}:
        return True
    command_args = args[1:]
    if command == "remote":
        return (
            not command_args
            or command_args in (["-v"], ["--verbose"])
            or command_args[:1] == ["get-url"]
        )
    if command == "branch":
        return (
            not command_args
            or command_args == ["--show-current"]
            or command_args[:1] in (["--list"], ["-l"])
        )
    if command == "tag":
        return not command_args or command_args[:1] in (["--list"], ["-l"])
    if command == "worktree":
        return command_args[:1] == ["list"]
    return False


def _is_safe_python(args: list[str]) -> bool:
    if not args:
        return False
    if args[:2] == ["-m", "pytest"]:
        return True
    if args[:2] == ["-m", "compileall"]:
        return True
    return False


def _is_safe_uv(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] == "run":
        return _safe_kind(args[1:]) in {"dev", "read"}
    return args[:1] in (["--version"], ["version"])


def _is_safe_node_package_command(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] in {"test", "run", "exec"}:
        script = args[1] if args[0] == "run" and len(args) > 1 else args[0]
        return bool(_SAFE_SCRIPT_RE.match(script))
    return False
