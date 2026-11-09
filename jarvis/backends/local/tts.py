"""Text-to-speech with Piper (ONNX). Output is resampled to the speaker's rate."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from jarvis.backends.local.errors import LocalBackendError
from jarvis.backends.local.speech import resample_to
from jarvis.config import SPEAKER_RATE

log = logging.getLogger(__name__)


def voices_dir() -> Path:
    from jarvis.config import _xdg

    return _xdg("XDG_DATA_HOME", ".local/share") / "voices"


def voice_path(name: str) -> Path:
    return voices_dir() / f"{name}.onnx"


class Synthesizer:
    def __init__(self, voice: str, out_rate: int = SPEAKER_RATE, loader=None):
        self._voice_name, self._out_rate, self._loader = voice, out_rate, loader
        self._voice = None

    def _load(self):
        if self._voice is None:
            path = voice_path(self._voice_name)
            if self._loader is None and not path.exists():
                raise LocalBackendError(
                    f"The voice {self._voice_name!r} isn't downloaded yet. Run `jarvis setup` to fetch it.")
            if self._loader is not None:
                self._voice = self._loader(path)
            else:
                from piper import PiperVoice

                self._voice = PiperVoice.load(path)
        return self._voice

    def warm_up(self) -> None:
        self._load()

    def _run(self, text: str) -> bytes:
        pcm, rate = [], self._out_rate
        for chunk in self._load().synthesize(text):
            rate = chunk.sample_rate
            pcm.append(chunk.audio_int16_bytes)
        return resample_to(b"".join(pcm), rate, self._out_rate)

    async def synthesize(self, text: str) -> bytes:
        return await asyncio.to_thread(self._run, text)
