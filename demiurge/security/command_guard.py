from __future__ import annotations

import re
import shlex
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal


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


_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SAFE_SCRIPT_RE = re.compile(r"^(test|tests|build|lint|check|typecheck|dev|preview)(:.+)?$")

_PROMPT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), key, reason)
    for pattern, key, reason in [
        (r"(?:^|[\s/])\.env(?:[.\s/]|$)", "sensitive-path", "touches environment files"),
        (r"(?:^|[\s/])config\.yaml(?:[\s/]|$)", "sensitive-path", "touches config.yaml"),
    ]
)


def review_command(command: str) -> CommandGuardDecision:
    normalized = _normalize(command)
    if not normalized:
        return CommandGuardDecision("prompt", "high", "empty command", "empty-command")

    split = _split_shell(normalized)
    hardline = _detect_hardline(normalized, split)
    if hardline is not None:
        reason, key = hardline
        return CommandGuardDecision("block", "critical", reason, key)

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

    prompt = _detect_promptable(normalized)
    if prompt is not None:
        reason, key = prompt
        return CommandGuardDecision("prompt", "high", reason, key)

    if "|" in split.separators and not all(_is_readonly_safe(tokens) for tokens in parsed):
        return CommandGuardDecision("prompt", "high", "pipeline is only auto-approved for read-only commands", "pipeline")

    for tokens in parsed:
        reason = _unsafe_path_reason(tokens)
        if reason is not None:
            return CommandGuardDecision("prompt", "high", reason, "path-outside-workspace")
        if not _is_safe_command(tokens):
            return CommandGuardDecision("prompt", "high", f"unrecognized terminal command: {_command_name(tokens)}", "unknown-command")

    return CommandGuardDecision("allow", "low", "safe terminal command", "safe-command")


def _normalize(command: str) -> str:
    command = _ANSI_RE.sub("", command)
    command = command.replace("\x00", "")
    command = unicodedata.normalize("NFKC", command)
    return command.strip()


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


def _detect_hardline(command: str, split: _ShellSplit) -> tuple[str, str] | None:
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
        index, command_name = _effective_command(tokens)
        if not command_name:
            continue
        args = tokens[index + 1 :]
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
        index, name = _effective_command(tokens)
        if not name:
            continue
        args = tokens[index + 1 :]
        if name in {"rm", "rmdir", "unlink"}:
            return ("delete files from the terminal", "file-delete")
        if name in {"cp", "mv", "mkdir", "touch", "chmod", "chown", "chgrp", "ln", "install"}:
            return ("write or mutate files", "file-write")
        if name == "tee":
            return ("write files through tee", "file-write")
        if name == "find" and _find_deletes(args):
            return ("find deleting files", "find-delete")
        if name == "git":
            prompt = _promptable_git(args)
            if prompt is not None:
                return prompt
        if name in {"curl", "wget"}:
            return ("download from the network", "network-download")
        if name in {"bash", "sh", "zsh", "ksh"} and _has_short_flag(args, "c"):
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
    parts = {part.lower() for part in PurePosixPath(token).parts}
    return any(part.startswith(".env") for part in parts) or "config.yaml" in parts


def _unsafe_path_reason(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in {"|", "&&", ";"} or token.startswith("-"):
            continue
        if token.startswith(("/", "~")):
            return "absolute or home-relative paths are not auto-approved in terminal commands"
        parts = PurePosixPath(token).parts
        if ".." in parts:
            return "parent-directory paths are not auto-approved in terminal commands"
    return None


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
    if command in {"status", "diff", "log", "show", "rev-parse", "ls-files", "grep", "remote", "describe"}:
        return True
    if command in {"branch", "tag", "worktree"}:
        return not any(arg in {"-D", "--delete", "delete", "add", "remove", "prune", "move", "repair"} for arg in args[1:])
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
