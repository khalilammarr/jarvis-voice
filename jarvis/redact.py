"""Keeps secrets out of what gets sent to Google.

Two best-effort layers:

1. ``denied_reference`` refuses commands that *name* a secret location
   (``cat ~/.ssh/id_ed25519``, ``cat .env``). Cheap, catches the obvious case.
2. ``scrub`` replaces secret-shaped strings in command output before it leaves
   the machine. Catches indirection that layer 1 misses
   (``cat $(ls ~/.s*/id*)``, a key printed by some unrelated tool).

Neither layer is a sandbox. A shell command written to evade them will succeed.
They exist to stop *accidental* leakage while the model is being helpful.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

# A path reference starts after one of these characters (or at the start)…
_BEFORE = r"(?:^|(?<=[\s'\"=(:;|&<>`]))"
# …and ends at one of these (or the end), so ~/.ssh does not match ~/.sshfs.
_AFTER = r"(?=$|[/\s'\";|&)<>`])"

# Any dotenv file, anywhere: .env, .env.local, sub/dir/.env.production
# Templates (.env.example, .env.sample, …) hold no secrets and are allowed.
_DOTENV = re.compile(r"(?:^|(?<=[\s'\"=(:;|&<>`/]))\.env"
                     r"(?!\.(?:example|sample|template|dist)\b)(?:\.[\w-]+)*" + _AFTER)


def _expand_home(command: str, home: Path) -> str:
    """Rewrite $HOME, ${HOME} and a leading ~ to the literal home path."""
    h = str(home)
    command = re.sub(r"\$\{HOME\}|\$HOME\b", h, command)
    return re.sub(r"(?:^|(?<=[\s'\"=(:;|&<>`]))~(?=$|[/\s'\"])", h, command)


def _deny_patterns(deny_paths: Iterable[str], home: Path) -> list[tuple[str, re.Pattern[str]]]:
    patterns = []
    for entry in deny_paths:
        absolute = Path(_expand_home(entry, home))
        alternatives = [re.escape(str(absolute))]
        if absolute.is_relative_to(home) and absolute != home:
            # Commands run with cwd=$HOME, so `cat .ssh/config` reaches the same file.
            alternatives.append(r"(?:\./)?" + re.escape(str(absolute.relative_to(home))))
        patterns.append((entry, re.compile(_BEFORE + "(?:" + "|".join(alternatives) + ")" + _AFTER)))
    return patterns


def denied_reference(command: str, deny_paths: Iterable[str], home: Path) -> str | None:
    """Return the deny-list entry ``command`` refers to, or None if it is clean."""
    normalised = _expand_home(command, home)
    for entry, pattern in _deny_patterns(deny_paths, home):
        if pattern.search(normalised):
            return entry
    if _DOTENV.search(normalised):
        return ".env files"
    return None


_NOT_ALREADY_REDACTED = r"(?!\[REDACTED)"

_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S)),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("google-api-key", re.compile(r"\bAQ\.[A-Za-z0-9_\-]{30,}")),  # newer AI Studio key format
    ("openai-or-anthropic-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+" + _NOT_ALREADY_REDACTED + r"[A-Za-z0-9._~+/=-]{8,}")),
)

# scheme://user:password@host
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@]+):" + _NOT_ALREADY_REDACTED + r"[^/\s@]+@")

# NAME=value, "name": "value", name: value — where NAME *ends* in a credential word,
# so token_count / password_hash / SECRET_FILE are left alone.
_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])([A-Za-z0-9_-]*?(?:api[-_]?key|secret|token|passw(?:or)?d|pwd))"
    r"(?![A-Za-z0-9_-])(['\"]?)\s*[=:]\s*"
    r"(?:(['\"])" + _NOT_ALREADY_REDACTED + r"(?!/)(?:(?!\3).)+\3"            # quoted value
    r"|" + _NOT_ALREADY_REDACTED + r"(?![/'\"])[^\s,;}][^\n,;}'\"]{5,})"      # bare value, to end of line
)


def scrub(text: str) -> tuple[str, int]:
    """Replace secret-shaped substrings. Returns (clean_text, replacements_made)."""
    total = 0
    for name, pattern in _TOKEN_PATTERNS:
        text, count = pattern.subn(f"[REDACTED:{name}]", text)
        total += count
    text, count = _URL_CREDENTIALS.subn(r"\1:[REDACTED]@", text)
    total += count
    text, count = _ASSIGNMENT.subn(lambda m: f"{m.group(1)}{m.group(2)}: [REDACTED]", text)
    return text, total + count
