"""The shared, slow-to-build parts of the local backend: models are loaded once per run."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.backends.local.endpoint import Endpointer
from jarvis.backends.local.errors import LocalBackendError
from jarvis.backends.local.llm import LLMClient
from jarvis.backends.local.stt import Transcriber
from jarvis.backends.local.tts import Synthesizer
from jarvis.config import Config
from jarvis.languages import local_voice_for

MISSING_EXTRA = ("The local backend needs extra packages. Install them with:\n"
                 "  uv sync --extra local        (or: pip install 'jarvis-voice[local]')")


@dataclass
class LocalRuntime:
    stt: Transcriber
    llm: LLMClient
    tts: Synthesizer

    def new_endpointer(self) -> Endpointer:
        return Endpointer()

    def warm_up(self) -> None:
        """Load Whisper and the voice now, so the first sentence isn't slow."""
        self.stt.warm_up()
        self.tts.warm_up()


def tts_voice_name(cfg: Config) -> str:
    return cfg.local.tts_voice or local_voice_for(cfg.language)


def create_runtime(cfg: Config) -> LocalRuntime:
    try:
        import faster_whisper  # noqa: F401
        import openai  # noqa: F401
        import piper  # noqa: F401
    except ImportError as exc:
        raise LocalBackendError(f"{MISSING_EXTRA}\n(missing: {exc.name})") from exc
    local = cfg.local
    return LocalRuntime(stt=Transcriber(local.stt_model, local.stt_device),
                        llm=LLMClient(local.llm_url, local.llm_model),
                        tts=Synthesizer(tts_voice_name(cfg)))
