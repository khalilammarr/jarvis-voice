"""Energy-based speech endpointing: turn a stream of mic frames into whole utterances.

Dependency-free on purpose. The noise floor adapts, so a fan or a quiet room both work.
Swap in a neural VAD later by keeping the same ``feed`` / ``in_speech`` / ``reset`` surface.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from jarvis.config import MIC_RATE

CALIBRATION_FRAMES = 4   # first frames only measure the room
MIN_RMS = 200.0          # never treat anything quieter than this as speech
FLOOR_RATIO = 3.0        # speech must be this many times louder than the noise floor
START_FRAMES = 2         # consecutive loud frames that open an utterance
HANGOVER_FRAMES = 8      # quiet frames (~0.64 s at 80 ms) that close it
MIN_SPEECH_FRAMES = 3    # shorter bursts (door slam, keyboard click) are dropped
PREROLL_FRAMES = 3       # audio kept from before the first loud frame
FLOOR_ALPHA = 0.1
MAX_FLOOR = 800.0        # a loud start (already talking) must not be mistaken for room noise


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame.astype(np.float64))))) if len(frame) else 0.0


class Endpointer:
    def __init__(self, max_utterance_s: float = 30.0, rate: int = MIC_RATE):
        self._max_s, self._rate = max_utterance_s, rate
        self.reset()

    def reset(self) -> None:
        """Forget any partial utterance (after Jarvis spoke, or a conversation ended)."""
        self._frames: list[np.ndarray] = []
        self._preroll: deque[np.ndarray] = deque(maxlen=PREROLL_FRAMES)
        self._candidate: list[np.ndarray] = []
        self._loud = 0
        self._quiet = 0
        self.in_speech = False

    def _threshold(self) -> float:
        return max(MIN_RMS, min(self._floor, MAX_FLOOR) * FLOOR_RATIO)

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """Add one frame. Returns the finished utterance (int16 samples) or None."""
        level = _rms(frame)
        if not hasattr(self, "_floor"):
            self._floor, self._seen = level, 0
        if self._seen < CALIBRATION_FRAMES:
            self._seen += 1
            self._floor = level if self._seen == 1 else (self._floor + level) / 2
            self._preroll.append(frame)
            return None
        loud = level > self._threshold()
        if not self.in_speech:
            return self._while_idle(frame, loud, level)
        return self._while_speaking(frame, loud)

    def _while_idle(self, frame: np.ndarray, loud: bool, level: float) -> None:
        if not loud:
            self._floor = max(1.0, (1 - FLOOR_ALPHA) * self._floor + FLOOR_ALPHA * level)
            self._candidate.clear()
            self._preroll.append(frame)
            return None
        self._candidate.append(frame)
        if len(self._candidate) >= START_FRAMES:
            self.in_speech = True
            self._frames = [*self._preroll, *self._candidate]
            self._loud, self._quiet = len(self._candidate), 0
            self._candidate.clear()
        return None

    def _while_speaking(self, frame: np.ndarray, loud: bool) -> np.ndarray | None:
        self._frames.append(frame)
        self._loud, self._quiet = (self._loud + 1, 0) if loud else (self._loud, self._quiet + 1)
        too_long = len(self._frames) * len(frame) / self._rate >= self._max_s
        if self._quiet < HANGOVER_FRAMES and not too_long:
            return None
        utterance = np.concatenate(self._frames) if self._loud >= MIN_SPEECH_FRAMES else None
        self.reset_keep_floor()
        return utterance

    def reset_keep_floor(self) -> None:
        floor, seen = self._floor, self._seen
        self.reset()
        self._floor, self._seen = floor, seen
