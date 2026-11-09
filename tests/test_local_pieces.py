"""Pure building blocks of the local backend: endpointing, sentence splitting, resampling."""

import numpy as np
import pytest

from jarvis.backends.local.endpoint import Endpointer
from jarvis.backends.local.speech import SentenceSplitter, clean_for_speech, resample_to


def frame(level: int, n: int = 1280) -> np.ndarray:
    rng = np.random.default_rng(level)
    return (rng.standard_normal(n) * level).astype(np.int16)


def feed(ep: Endpointer, levels: list[int]):
    return [ep.feed(frame(level)) for level in levels]


def test_silence_never_produces_an_utterance():
    assert not any(r is not None for r in feed(Endpointer(), [20] * 50))


def test_speech_then_silence_yields_one_utterance_with_preroll():
    ep = Endpointer()
    out = feed(ep, [20] * 10 + [3000] * 10 + [20] * 12)
    utterances = [u for u in out if u is not None]
    assert len(utterances) == 1
    # 10 speech frames + ~3 pre-roll frames + trailing hangover, never the whole recording
    assert 10 * 1280 <= len(utterances[0]) <= 24 * 1280


def test_a_click_shorter_than_min_speech_is_ignored():
    ep = Endpointer()
    assert not any(u is not None for u in feed(ep, [20] * 10 + [3000] + [20] * 20))


def test_pauses_inside_a_sentence_do_not_split_it():
    ep = Endpointer()
    out = feed(ep, [20] * 5 + [3000] * 6 + [20] * 3 + [3000] * 6 + [20] * 12)
    assert len([u for u in out if u is not None]) == 1


def test_in_speech_flag_tracks_state():
    ep = Endpointer()
    feed(ep, [20] * 5)
    assert not ep.in_speech
    feed(ep, [3000] * 4)
    assert ep.in_speech


def test_very_long_speech_is_cut_at_the_cap():
    ep = Endpointer(max_utterance_s=2.0)
    out = feed(ep, [3000] * 60)
    assert any(u is not None for u in out)


def test_reset_discards_a_partial_utterance():
    ep = Endpointer()
    feed(ep, [3000] * 5)
    ep.reset()
    assert not ep.in_speech
    assert not any(u is not None for u in feed(ep, [20] * 15))


def test_noise_floor_adapts_to_a_noisy_room():
    ep = Endpointer()
    out = feed(ep, [400] * 40 + [4000] * 8 + [400] * 12)  # constant fan noise, then speech
    assert len([u for u in out if u is not None]) == 1


@pytest.mark.parametrize("deltas, expected", [
    (["Hello there. How are", " you today? Fine."], ["Hello there.", "How are you today?"]),
    (["No end punctuation"], []),
    (["Version 3.5 is out. Great!"], ["Version 3.5 is out."]),
    (["Line one\n\nLine two"], ["Line one"]),
])
def test_sentence_splitter_emits_complete_sentences_only(deltas, expected):
    splitter, got = SentenceSplitter(), []
    for delta in deltas:
        got += splitter.feed(delta)
    assert got == expected


def test_sentence_splitter_flush_returns_the_rest():
    splitter = SentenceSplitter()
    splitter.feed("Almost done")
    assert splitter.flush() == "Almost done"
    assert splitter.flush() == ""


def test_think_blocks_are_never_spoken():
    splitter, got = SentenceSplitter(), []
    for delta in ["<think>plan the answer.", " more.</think>Sure thing. Done."]:
        got += splitter.feed(delta)
    assert got == ["Sure thing."] and splitter.flush() == "Done."


@pytest.mark.parametrize("raw, spoken", [
    ("**Bold** and `code`", "Bold and code"),
    ("# Title\n- item one", "Title item one"),
    ("see [docs](http://x.io/a)", "see docs"),
    ("fine 👍 ok", "fine ok"),
])
def test_clean_for_speech_strips_markup(raw, spoken):
    assert clean_for_speech(raw) == spoken


def test_resample_changes_length_proportionally():
    pcm = (np.sin(np.linspace(0, 200, 22_050)) * 8000).astype(np.int16).tobytes()
    out = np.frombuffer(resample_to(pcm, 22_050, 24_000), dtype=np.int16)
    assert abs(len(out) - 24_000) <= 2


def test_resample_same_rate_is_identity():
    pcm = b"\x01\x00\x02\x00"
    assert resample_to(pcm, 24_000, 24_000) == pcm
