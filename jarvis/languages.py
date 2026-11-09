"""The choices the setup wizard offers: languages and personas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    piper_voice: str  # default local TTS voice (downloaded by `jarvis setup`)


AUTO = "auto"
_AUTO_NAME = "the language the user speaks"

LANGUAGES: dict[str, Language] = {
    AUTO: Language(_AUTO_NAME, "en_US-lessac-medium"),
    "en": Language("English", "en_US-lessac-medium"),
    "fr": Language("French", "fr_FR-siwis-medium"),
    "ar": Language("Arabic", "ar_JO-kareem-medium"),
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
