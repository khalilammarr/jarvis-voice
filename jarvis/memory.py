"""Long-term memory: facts the user asked Jarvis to remember, in a Markdown file they can edit.

One fact per line, ``- [YYYY-MM-DD] fact``. Every fact is sent to Gemini in the system
prompt, so secret-looking facts are refused rather than stored.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path

from jarvis.fileio import write_atomic
from jarvis.redact import scrub

MAX_FACT_CHARS = 300
MAX_FACTS = 200
MIN_FORGET_CHARS = 3
MAX_FORGET_MATCHES = 3  # a misheard "forget the..." must not wipe the file
PROMPT_FACTS = 60  # most recent facts injected into each conversation

_HEADER = "# Jarvis memory\n\nOne fact per line. Edit or delete freely.\n\n"
_DATE_PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2}\]\s*")
# Spoken secrets have no KEY=value shape for redact.scrub to catch.
_SPOKEN_SECRET = re.compile(
    r"(?i)\b(?:pass(?:word|wd|code|phrase)s?|pins?|secrets?|api[ -]?keys?|tokens?|private keys?|"
    r"(?:recovery|backup|2fa|otp|mfa) codes?|seed phrases?|cvv|iban)\b|\d(?: ?\d){11,}")


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


class MemoryStore:
    def __init__(self, path: Path, today: Callable[[], date] = date.today):
        self.path = path
        self._today = today
        self._lock = threading.Lock()  # tool calls run in worker threads

    def facts(self) -> list[str]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [line[2:].strip() for line in lines if line.startswith("- ") and line[2:].strip()]

    def _save(self, facts: list[str]) -> None:
        write_atomic(self.path, _HEADER + "".join(f"- {fact}\n" for fact in facts))

    def remember(self, fact: str) -> dict:
        fact = " ".join(str(fact).split())
        if not fact:
            return {"error": "Nothing to remember."}
        if len(fact) > MAX_FACT_CHARS:
            return {"error": f"Too long; keep a fact under {MAX_FACT_CHARS} characters."}
        if scrub(fact)[1] or _SPOKEN_SECRET.search(fact):
            return {"error": "That looks like a secret (key, token or password). Not stored."}
        with self._lock:
            facts = self.facts()
            if any(_normalise(_DATE_PREFIX.sub("", f)) == _normalise(fact) for f in facts):
                return {"status": "already known", "fact": fact}
            updated = [*facts, f"[{self._today().isoformat()}] {fact}"][-MAX_FACTS:]
            self._save(updated)
        return {"status": "remembered", "fact": fact}

    def recall(self, query: str | None = None) -> dict:
        words = _normalise(query or "").split()
        facts = [f for f in self.facts() if all(w in f.lower() for w in words)]
        return {"facts": facts} if facts or not words else {"facts": [], "note": "Nothing matches."}

    def forget(self, query: str) -> dict:
        needle = _normalise(str(query))
        if len(needle) < MIN_FORGET_CHARS:
            return {"error": "Say more precisely what to forget."}
        with self._lock:
            facts = self.facts()
            removed = [f for f in facts if needle in _normalise(f)]
            if not removed:
                return {"error": f"No memory matches '{query}'."}
            if len(removed) > MAX_FORGET_MATCHES:
                return {"error": f"'{query}' matches {len(removed)} memories; forget them one at a time "
                                 "with more specific text.", "matches": removed}
            self._save([f for f in facts if f not in removed])
        return {"forgot": removed}

    def as_prompt(self) -> str:
        # The file is hand-editable, so scrub what the user may have pasted in.
        return scrub("\n".join(f"- {fact}" for fact in self.facts()[-PROMPT_FACTS:]))[0]
