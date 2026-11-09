"""Unrestricted shell execution for the assistant, by the owner's explicit choice.

Every command is written to an audit log before anything else happens. Two
safety nets sit in front of execution; neither is a sandbox:

* **guard** refuses a short list of unrecoverable operations: recursive
  deletes of ``/``, ``$HOME`` or system directories, ``mkfs``, ``dd`` or a
  redirect onto a block device, ``wipefs``, fork bombs. It exists to catch a
  *misheard* request, not an adversary. ``JARVIS_GUARD=0`` turns it off.
* **deny paths** refuse commands that name secret locations (see ``redact``).

Output is scrubbed of secret-shaped strings and truncated before it is
returned, because it is about to be sent to a cloud model.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from jarvis.redact import denied_reference, scrub


@dataclass(frozen=True)
class ShellPolicy:
    guard: bool
    deny_paths: tuple[str, ...]
    timeout_s: float
    max_output_chars: int
    log_path: Path
    home: Path


@dataclass(frozen=True)
class ShellResult:
    command: str
    exit_code: int | None
    output: str
    refused: str | None = None
    redactions: int = 0

    def as_tool_response(self) -> dict:
        if self.refused:
            return {"error": f"Refused: {self.refused}"}
        response: dict = {"exit_code": self.exit_code, "output": self.output or "(no output)"}
        if self.redactions:
            response["note"] = f"{self.redactions} secret-looking value(s) were redacted from the output."
        return response


# --------------------------------------------------------------------------- guard

_SYSTEM_DIRS = ("bin", "boot", "dev", "etc", "home", "lib", "lib64", "opt",
                "root", "sbin", "srv", "usr", "var")
_SAFE_DEVICES = {"/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/tty"}
_WRAPPERS = {"sudo", "doas", "command", "nice", "nohup", "exec", "time", "env", "builtin"}
# Wrapper options that consume the next word (`sudo -u root rm …`).
_WRAPPER_OPTION_ARGS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D", "-R", "-T"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "--unset", "--chdir"},
    "nice": {"-n", "--adjustment"},
}
# Shell keywords that can precede a command inside compound statements.
_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
_SEPARATORS = {";", "&", "&&", "|", "||", "(", ")", "\n"}
_REDIRECTS = {">", ">>", "<", "<<", ">&", "&>", ">|"}

_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")
_BLOCK_DEVICE_REDIRECT = re.compile(
    r">\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|xvd[a-z]|mmcblk\d+|hd[a-z])")


def _segments(command: str) -> list[list[str]]:
    """Split a command line into simple commands, respecting quotes."""
    command = command.replace("\n", " ; ")  # shlex treats newlines as plain whitespace
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a naive split
        tokens = re.split(r"(\s+|;|&&|\|\||\||&)", command)
        tokens = [t for t in tokens if t and not t.isspace()]

    segments, current, skip_next = [], [], False
    for token in tokens:
        if skip_next:
            skip_next = False
        elif token in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        elif token in _REDIRECTS:
            skip_next = True
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _strip_wrappers(argv: list[str]) -> list[str]:
    """Drop keywords, wrappers (with their options) and VAR=value prefixes."""
    argv = list(argv)
    while argv:
        head = argv[0]
        if head in _KEYWORDS:
            argv.pop(0)
        elif head in _WRAPPERS:
            argv.pop(0)
            takes_value = _WRAPPER_OPTION_ARGS.get(head, set())
            while argv and argv[0].startswith("-"):
                option = argv.pop(0)
                if option in takes_value and argv:
                    argv.pop(0)
        elif "=" in head and not head.startswith(("-", "/", ".")):
            argv.pop(0)
        else:
            break
    return argv


def _normalise_target(arg: str, home: Path, cwd: str) -> str:
    """Resolve an operand to an absolute, normalised path; a trailing glob means its parent."""
    h = str(home)
    arg = arg.replace("${HOME}", h).replace("$HOME", h)
    if arg == "~" or arg.startswith("~/"):
        arg = h + arg[1:]
    for glob in ("/*", "/.*"):
        if arg.endswith(glob):
            arg = arg[: -len(glob)] or "/"
    if arg in {"*", ".*"}:
        arg = "."
    path = os.path.normpath(os.path.join(cwd, arg))
    return "/" + path.lstrip("/") if path.startswith("//") else path


_CRITICAL_EXTRA = ("/var/lib", "/var/log", "/usr/lib", "/usr/bin", "/usr/share", "/usr/local")


def _is_critical(target: str, home: Path) -> bool:
    critical = {"/", str(home)} | {f"/{d}" for d in _SYSTEM_DIRS} | set(_CRITICAL_EXTRA)
    return target in critical


def _split_flags(args: list[str]) -> tuple[list[str], list[str]]:
    flags, operands, end_of_flags = [], [], False
    for arg in args:
        if end_of_flags or not arg.startswith("-") or arg == "-":
            operands.append(arg)
        elif arg == "--":
            end_of_flags = True
        else:
            flags.append(arg)
    return flags, operands


def _is_recursive(flags: list[str]) -> bool:
    return any(f == "--recursive" or (not f.startswith("--") and set("rR") & set(f[1:])) for f in flags)


def _check_segment(argv: list[str], home: Path, cwd: str) -> str | None:
    argv = _strip_wrappers(argv)
    if not argv:
        return None
    name, args = os.path.basename(argv[0]), argv[1:]
    if name in _SHELLS and "-c" in args[:-1]:  # bash -c 'rm -rf ~'
        return catastrophic_reason(args[args.index("-c") + 1], home)
    flags, operands = _split_flags(args)
    targets = [_normalise_target(o, home, cwd) for o in operands]

    if name == "rm":
        if "--no-preserve-root" in flags:
            return "rm --no-preserve-root"
        if _is_recursive(flags) and any(_is_critical(t, home) for t in targets):
            return "recursive delete of / , $HOME or a system directory"
    if name in {"chmod", "chown", "chgrp"} and _is_recursive(flags) and any(
            _is_critical(t, home) for t in targets[1:]):
        return f"recursive {name} of / , $HOME or a system directory"
    if name.startswith("mkfs"):
        return "formatting a filesystem"
    if name in {"wipefs", "blkdiscard"}:
        return f"{name} on a block device"
    if name == "shred" and any(t.startswith("/dev/") for t in targets):
        return "shred on a block device"
    if name == "dd":
        for arg in args:
            if arg.startswith("of=/dev/") and arg[3:] not in _SAFE_DEVICES:
                return "dd onto a block device"
    return None


def catastrophic_reason(command: str, home: Path) -> str | None:
    """Return why ``command`` is unrecoverable, or None if the guard allows it."""
    if _FORK_BOMB.search(command):
        return "fork bomb"
    if _BLOCK_DEVICE_REDIRECT.search(command):
        return "redirecting output onto a block device"
    cwd = str(home)  # run_shell starts every command in $HOME; follow simple `cd`s from there
    for segment in _segments(command):
        argv = _strip_wrappers(segment)
        if argv and argv[0] == "cd":
            target = argv[1] if len(argv) > 1 else "~"
            if target != "-":
                cwd = _normalise_target(target, home, cwd)
            continue
        reason = _check_segment(segment, home, cwd)
        if reason:
            return reason
    return None


# ------------------------------------------------------------------- execution

def check_command(command: str, policy: ShellPolicy) -> str | None:
    """Return a refusal message, or None if ``command`` may run."""
    if policy.guard:
        reason = catastrophic_reason(command, policy.home)
        if reason:
            return f"blocked by the safety guard ({reason}). The user can disable it with JARVIS_GUARD=0."
    denied = denied_reference(command, policy.deny_paths, policy.home)
    if denied:
        return f"{denied} is off-limits to the assistant."
    return None


def audit(log_path: Path, kind: str, result: ShellResult) -> None:
    """Append one JSON line per command. Output is never logged: it may hold secrets."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(log_path.parent, 0o700)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind,
             **{k: v for k, v in asdict(result).items() if k != "output"}}
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n…[{dropped} characters truncated]…\n{text[-half:]}"


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _execute(command: str, policy: ShellPolicy) -> ShellResult:
    proc = subprocess.Popen(
        ["bash", "-c", command], cwd=policy.home, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
        start_new_session=True,  # own process group, so a timeout kills grandchildren too
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=policy.timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:  # a daemonised child is still holding the pipes
            stdout, stderr = "", ""

    output = stdout.rstrip()
    if stderr.strip():
        output += ("\n" if output else "") + "[stderr]\n" + stderr.rstrip()
    if timed_out:
        output += f"\n[timed out after {policy.timeout_s:g}s and was killed]"

    clean, redactions = scrub(output)
    return ShellResult(command=command, exit_code=None if timed_out else proc.returncode,
                       output=_truncate(clean, policy.max_output_chars), redactions=redactions)


def run_shell(command: str, policy: ShellPolicy) -> ShellResult:
    refusal = check_command(command, policy)
    result = (ShellResult(command=command, exit_code=None, output="", refused=refusal)
              if refusal else _execute(command, policy))
    audit(policy.log_path, "shell", result)
    return result
