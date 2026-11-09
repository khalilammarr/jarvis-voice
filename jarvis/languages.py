"""The choices the setup wizard offers: languages and personas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    piper_voice: str  # default local TTS voice (downloaded by `jarvis setup`)
    stt_code: str | None = None  # Whisper language code when it differs from the key (dialects)
    hint: str = ""               # extra instruction for the model, e.g. which dialect to speak


AUTO = "auto"
_AUTO_NAME = "the language the user speaks"

LANGUAGES: dict[str, Language] = {
    AUTO: Language(_AUTO_NAME, "en_US-lessac-medium"),
    "en": Language("English", "en_US-lessac-medium"),
    "fr": Language("French", "fr_FR-siwis-medium"),
    "ar": Language("Arabic", "ar_JO-kareem-medium"),
    # No Whisper model or Piper voice exists for Tunisian: local mode listens and speaks standard
    # Arabic. Gemini follows the dialect instruction and is the better choice for Derja.
    "ar-tn": Language(
        "Tunisian Arabic (Derja)", "ar_JO-kareem-medium", stt_code="ar",
        hint="Speak Tunisian Arabic, the Derja dialect used in Tunis, not Modern Standard Arabic. "
             "Mix in French words the way Tunisians naturally do. Write Arabic script, never Latin letters."),
    "es": Language("Spanish", "es_ES-davefx-medium"),
    "de": Language("German", "de_DE-thorsten-medium"),
    "it": Language("Italian", "it_IT-riccardo-x_low"),
    "pt": Language("Portuguese", "pt_BR-faber-medium"),
    "nl": Language("Dutch", "nl_BE-nathalie-medium"),
    "ru": Language("Russian", "ru_RU-denis-medium"),
    "tr": Language("Turkish", "tr_TR-dfki-medium"),
    "zh": Language("Chinese", "zh_CN-huayan-medium"),
}

PERSONAS: dict[str, str] = {
    "sidekick": "Casual, quick, a little banter. Like a tech-savvy friend sitting next to you.",
    "friendly": "Warm and patient. Explains things plainly, good for everyday use.",
    "concise": "Neutral and terse. Answers only, no small talk.",
}


def language_name(code: str) -> str:
    return LANGUAGES[code].name


def local_voice_for(code: str) -> str:
    return LANGUAGES[code].piper_voice


def stt_code_for(code: str) -> str | None:
    """The language code Whisper should be told, or None to let it detect."""
    if code == AUTO:
        return None
    return LANGUAGES[code].stt_code or code


def hint_for(code: str) -> str:
    return LANGUAGES[code].hint
