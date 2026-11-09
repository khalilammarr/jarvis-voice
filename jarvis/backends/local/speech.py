"""Text and audio helpers for speaking an LLM's streamed reply aloud."""

from __future__ import annotations

import re

import numpy as np

_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_MARKUP = re.compile(r"[*`#>]|^\s*[-•]\s+", re.M)
_SPACES = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    """Strip markdown, links and emoji that a TTS voice would read out loud."""
    text = _LINK.sub(r"\1", text)
    text = _EMOJI.sub("", _MARKUP.sub("", text))
    return _SPACES.sub(" ", text).strip()


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of ``tag``."""
    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


class SentenceSplitter:
    """Cuts a token stream into sentences, so TTS can start before the reply is finished.

    Reasoning models wrap their thoughts in <think>…</think>; those are dropped here.
    """

    def __init__(self) -> None:
        self._raw = ""        # not yet scanned for think tags
        self._text = ""       # visible text waiting for a sentence end
        self._in_think = False

    def _absorb(self) -> None:
        while self._raw:
            tag = _THINK_CLOSE if self._in_think else _THINK_OPEN
            at = self._raw.find(tag)
            if at >= 0:
                if not self._in_think:
                    self._text += self._raw[:at]
                self._raw, self._in_think = self._raw[at + len(tag):], not self._in_think
                continue
            keep = _partial_suffix(self._raw, tag)
            done, self._raw = self._raw[:len(self._raw) - keep], self._raw[len(self._raw) - keep:]
            if not self._in_think:
                self._text += done
            return

    def feed(self, delta: str) -> list[str]:
        self._raw += delta
        self._absorb()
        parts = _SENTENCE_END.split(self._text)
        self._text = parts.pop()  # the unfinished tail
        return [s for s in (clean_for_speech(p) for p in parts) if s]

    def flush(self) -> str:
        self._absorb()
        rest, self._text, self._raw = clean_for_speech(self._text), "", ""
        return rest


def resample_to(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of mono int16 PCM. Fine for speech."""
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    out_len = int(round(len(samples) * dst_rate / src_rate))
    positions = np.linspace(0, len(samples) - 1, out_len)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.int16).tobytes()
