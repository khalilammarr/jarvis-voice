from types import SimpleNamespace

from jarvis.models import FALLBACK_LIVE_MODEL, choose_model, live_models, pick_live_model


def _model(name, actions=("generateContent", "bidiGenerateContent")):
    return SimpleNamespace(name=name, supported_actions=list(actions))


def test_ignores_models_without_live_support():
    models = [_model("models/gemini-9.0-pro", ["generateContent"]), _model("models/gemini-2.5-flash-live")]
    assert live_models(models) == ["models/gemini-2.5-flash-live"]


def test_prefers_newest_version():
    models = [_model("models/gemini-2.5-flash-native-audio-preview"), _model("models/gemini-3.8-live")]
    assert pick_live_model(models) == "models/gemini-3.8-live"


def test_prefers_stable_and_non_thinking_within_a_version():
    models = [
        _model("models/gemini-3.8-live-extended-thinking"),
        _model("models/gemini-3.8-live-preview"),
        _model("models/gemini-3.8-live"),
    ]
    assert pick_live_model(models) == "models/gemini-3.8-live"


def test_returns_none_when_no_live_models():
    assert pick_live_model([_model("models/text-only", ["generateContent"])]) is None


def test_tolerates_missing_supported_actions():
    assert pick_live_model([SimpleNamespace(name="models/x")]) is None


def test_choose_model_prefers_explicit_setting():
    assert choose_model("models/mine", [_model("models/gemini-3.8-live")]) == "models/mine"


def test_choose_model_prefers_the_free_tier_model_over_newer_paid_ones():
    listing = [_model("models/gemini-3.8-live"), _model("models/gemini-2.5-flash-native-audio-latest")]
    assert choose_model(None, listing) == "gemini-2.5-flash-native-audio-latest"


def test_choose_model_uses_best_advertised_model_without_the_free_one():
    assert choose_model(None, [_model("models/gemini-3.8-live")]) == "models/gemini-3.8-live"


def test_specialised_live_models_are_never_picked():
    listing = [_model("models/gemini-3.5-transcribe-live"), _model("models/gemini-3.5-live-translate-preview"),
               _model("models/gemini-3.1-flash-live-preview")]
    assert pick_live_model(listing) == "models/gemini-3.1-flash-live-preview"


def test_choose_model_falls_back_when_google_advertises_none():
    listing = [_model("models/gemini-3.8-flash", ["generateContent"])]
    assert choose_model(None, listing) == FALLBACK_LIVE_MODEL == "gemini-2.5-flash-native-audio-latest"
