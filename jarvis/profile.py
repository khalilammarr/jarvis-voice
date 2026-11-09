"""Who the user is (me.md), the once-a-day briefing gate, and the status file the bar reads."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from jarvis.fileio import write_atomic
from jarvis.redact import scrub

log = logging.getLogger(__name__)

MAX_PROFILE_CHARS = 4_000
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLANK_RUNS = re.compile(r"\n{3,}")

DRAFT_PROFILE = """\
# About me

<!-- Jarvis reads this file at the start of every conversation. Write it like a note to a
     teammate. Everything here is sent to the language model: with the Gemini backend that
     means Google (free tier keys may be used for training), so keep anything confidential
     OUT of it. With the local backend it never leaves this machine.
     Lines inside these comment markers are ignored. -->

## Basics
- Name / what to call me:
- City (for weather and time zone):
- Languages I speak:

## Setup
- Operating system / desktop:
- Browser:
- Editor / terminal:

## What I do
- Work / studies:
- Currently working on:

## How I like to work
- Usual hours:
- Music while working:
- Things Jarvis should nag me about (breaks, water, sleep):

## Goals right now
-
"""


def ensure_profile(path: Path) -> bool:
    """Create the draft profile if missing. Returns True when it was just created."""
    if path.exists():
        return False
    write_atomic(path, DRAFT_PROFILE)
    return True


def load_profile(path: Path, max_chars: int = MAX_PROFILE_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = _BLANK_RUNS.sub("\n\n", _COMMENT.sub("", text)).strip()
    return scrub(text)[0][:max_chars]


class BriefingGate:
    """Remembers the last day a briefing was given, so it happens once per day."""

    def __init__(self, path: Path):
        self._path = path

    def due(self, day: date) -> bool:
        try:
            return self._path.read_text().strip() != day.isoformat()
        except OSError:
            return True

    def mark(self, day: date) -> None:
        write_atomic(self._path, day.isoformat() + "\n", mode=0o644)


def write_state(path: Path | None, state: str) -> None:
    """Publish 'listening' / 'talking' for the Quickshell bar widget. Never fatal."""
    if path is None:
        return
    try:
        write_atomic(path, state + "\n", mode=0o644)
    except OSError as exc:
        log.debug("could not write state file %s: %s", path, exc)
