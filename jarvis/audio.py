"""Microphone and speaker streams (PortAudio via sounddevice, routed through PipeWire)."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from jarvis.config import MIC_FRAME, MIC_RATE, SPEAKER_RATE


def _device(spec: str | None) -> int | str | None:
    """JARVIS_*_DEVICE may be an index ("4") or a name fragment ("Mic1")."""
    if spec is None:
        return None
    return int(spec) if spec.isdigit() else spec


class PlaybackBuffer:
    """Thread-safe PCM FIFO between the asyncio side (writer) and the audio callback (reader).

    ``busy`` stays true for ``tail_s`` after the last sample is handed to the sound
    card, covering output latency and room echo, so half-duplex mode does not let the
    model hear the tail end of its own voice.
    """

    def __init__(self, tail_s: float = 0.35, clock: Callable[[], float] = time.monotonic):
        self._data = bytearray()
        self._lock = threading.Lock()
        self._tail_s = tail_s
        self._clock = clock
        self._last_played = -math.inf

    def write(self, pcm: bytes) -> None:
        with self._lock:
            self._data.extend(pcm)

    def read_into(self, out) -> None:
        """Fill ``out`` completely: queued audio first, silence for the remainder."""
        size = len(out)
        with self._lock:
            chunk = bytes(self._data[:size])
            del self._data[:size]
        if chunk:
            self._last_played = self._clock()
        out[: len(chunk)] = chunk
        out[len(chunk):] = b"\x00" * (size - len(chunk))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    @property
    def busy(self) -> bool:
        with self._lock:
            pending = bool(self._data)
        return pending or (self._clock() - self._last_played) < self._tail_s


def _tone(freqs: tuple[float, ...], note_s: float = 0.09, rate: int = SPEAKER_RATE, gain: float = 0.22) -> bytes:
    t = np.arange(int(rate * note_s)) / rate
    fade = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.01)  # 10 ms fades, no clicks
    notes = [np.sin(2 * np.pi * f * t) * fade for f in freqs]
    return (np.concatenate(notes) * gain * 32767).astype(np.int16).tobytes()


CHIME_WAKE = _tone((880.0, 1318.5))
CHIME_SLEEP = _tone((1318.5, 880.0))
CHIME_ERROR = _tone((330.0, 330.0, 220.0))


class Speaker:
    def __init__(self, device: str | None = None, rate: int = SPEAKER_RATE):
        self.buffer = PlaybackBuffer()
        self._stream = sd.RawOutputStream(
            samplerate=rate, channels=1, dtype="int16", device=_device(device),
            callback=self._callback, latency="low")

    def _callback(self, outdata, frames, time_info, status) -> None:
        self.buffer.read_into(outdata)

    def __enter__(self) -> "Speaker":
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    def play(self, pcm: bytes) -> None:
        self.buffer.write(pcm)

    def flush(self) -> None:
        self.buffer.clear()

    @property
    def busy(self) -> bool:
        return self.buffer.busy


class Microphone:
    """Async source of 80 ms int16 frames. Drops the oldest frame rather than block audio."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device: str | None = None,
                 rate: int = MIC_RATE, frame: int = MIC_FRAME, max_frames: int = 64):
        self._loop = loop
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=max_frames)
        self._stream = sd.InputStream(
            samplerate=rate, channels=1, dtype="int16", blocksize=frame,
            device=_device(device), callback=self._callback)

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            self._loop.call_soon_threadsafe(self._enqueue, indata[:, 0].copy())
        except RuntimeError:  # event loop already closed during shutdown
            pass

    def _enqueue(self, frame: np.ndarray) -> None:
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(frame)

    def __enter__(self) -> "Microphone":
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    async def read(self) -> np.ndarray:
        return await self._queue.get()

    def drain(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
