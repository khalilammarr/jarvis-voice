"""Terminal transcript: what you said, what Jarvis said, which tools it ran."""

from __future__ import annotations

import sys
from typing import TextIO

_DIM, _RESET, _RED = "\033[2m", "\033[0m", "\033[31m"
_LABELS = {"you": "\033[36mYou\033[0m", "jarvis": "\033[33mJarvis\033[0m"}


class Transcript:
    def __init__(self, out: TextIO = sys.stdout):
        self._out = out
        self._role: str | None = None

    def _line_break(self) -> None:
        if self._role is not None:
            self._out.write("\n")
        self._role = None

    def say(self, role: str, text: str) -> None:
        if role != self._role:
            self._line_break()
            self._out.write(f"{_LABELS[role]}: ")
            self._role = role
        self._out.write(text)
        self._out.flush()

    def end_turn(self) -> None:
        self._line_break()
        self._out.flush()

    def tool(self, name: str, args: dict) -> None:
        self._line_break()
        detail = args.get("command") or args.get("query") or args.get("action") or ""
        self._out.write(f"{_DIM}  ⚙ {name}  {detail}{_RESET}\n")
        self._out.flush()

    def info(self, message: str) -> None:
        self._line_break()
        self._out.write(f"{_DIM}{message}{_RESET}\n")
        self._out.flush()

    def error(self, message: str) -> None:
        self._line_break()
        self._out.write(f"{_RED}{message}{_RESET}\n")
        self._out.flush()
