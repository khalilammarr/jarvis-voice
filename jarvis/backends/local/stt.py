"""Speech-to-text with faster-whisper (CTranslate2). The model loads on first use."""

from __future__ import annotations

import asyncio
import logging

import numpy as np

log = logging.getLogger(__name__)

_NOISE_ONLY = {"", ".", "..", "...", "you", "uh", "um", "hmm"}  # what Whisper invents for near-silence


class Transcriber:
    def __init__(self, model: str = "small", device: str = "auto", model_factory=None):
        self._name, self._device, self._factory = model, device, model_factory
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        if self._factory is not None:
            self._model = self._factory(self._name, self._device)
            return self._model
        from faster_whisper import WhisperModel

        device, compute = self._device, "int8"
        if device in ("auto", "cuda"):
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                device, compute = "cuda", "int8_float16"
            elif device == "auto":
                device = "cpu"
        log.info("loading Whisper %s on %s (%s)", self._name, device, compute)
        self._model = WhisperModel(self._name, device=device, compute_type=compute)
        return self._model

    def warm_up(self) -> None:
        self._load()

    def _run(self, pcm: np.ndarray, language: str | None) -> str:
        audio = pcm.astype(np.float32) / 32768.0
        segments, _info = self._load().transcribe(audio, language=language, beam_size=1,
                                                  condition_on_previous_text=False)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return "" if text.lower().strip(" .!?,") in _NOISE_ONLY else text

    async def transcribe(self, pcm: np.ndarray, language: str | None) -> str:
        return await asyncio.to_thread(self._run, pcm, language)
