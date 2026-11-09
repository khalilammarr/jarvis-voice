"""Pick a Gemini Live model automatically, so model renames don't break startup."""

from __future__ import annotations

import re
from collections.abc import Iterable

LIVE_ACTION = "bidiGenerateContent"
# The default v1beta endpoint returns HTTP 404 for Live and hides Live models from models.list().
LIVE_API_VERSION = "v1alpha"
# Verified 2026-11-07: the only Live model with free-tier quota. Newer ones (gemini-3.8-live,
# gemini-3.1-flash-live-preview) connect, then fail with "exceeded your current quota".
FALLBACK_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
# Live models that aren't conversational assistants.
_SPECIALISED = ("transcribe", "translate", "robotics")


def _version(name: str) -> float:
    match = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(match.group(1)) if match else 0.0


def live_models(models: Iterable) -> list[str]:
    """Names of every model that supports the Live (bidirectional) API."""
    return [m.name for m in models if LIVE_ACTION in (getattr(m, "supported_actions", None) or [])]


def pick_live_model(models: Iterable) -> str | None:
    """Newest Live model, preferring native audio, stable over preview, fast over thinking."""
    names = [n for n in live_models(models) if not any(s in n for s in _SPECIALISED)]
    if not names:
        return None

    def rank(name: str) -> tuple:
        return (
            _version(name),
            "native-audio" in name or "-live" in name,
            "flash" in name,
            "thinking" not in name,
            "preview" not in name and "exp" not in name,
            name,
        )

    return max(names, key=rank)


def choose_model(configured: str | None, models: Iterable) -> str:
    """JARVIS_MODEL if set, else the free-tier model, else the best advertised Live model."""
    if configured:
        return configured
    models = list(models)
    if any(name.removeprefix("models/") == FALLBACK_LIVE_MODEL for name in live_models(models)):
        return FALLBACK_LIVE_MODEL
    return pick_live_model(models) or FALLBACK_LIVE_MODEL
