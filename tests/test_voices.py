import asyncio
import contextlib
import stat
from types import SimpleNamespace

import pytest
from google.genai import types

from jarvis.voices import VOICES, audition, parse_voices, run_audition, set_env_value


def test_voice_catalogue_includes_the_current_default():
    assert "Charon" in VOICES and len(VOICES) == len(set(VOICES)) >= 30


def test_parse_voices_defaults_to_all_and_normalises_case():
    assert parse_voices(None) == list(VOICES)
    assert parse_voices("puck, KORE") == ["Puck", "Kore"]


def test_parse_voices_rejects_unknown_names():
    with pytest.raises(ValueError, match="Bob"):
        parse_voices("Puck,Bob")


def test_set_env_value_replaces_in_place_and_keeps_other_lines_and_perms(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=abc\n# Gemini voice\nJARVIS_VOICE=Charon\nX=1\n")
    env.chmod(0o600)
    set_env_value(env, "JARVIS_VOICE", "Puck")
    assert env.read_text() == "GEMINI_API_KEY=abc\n# Gemini voice\nJARVIS_VOICE=Puck\nX=1\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_set_env_value_appends_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1")
    set_env_value(env, "JARVIS_VOICE", "Kore")
    assert env.read_text() == "A=1\nJARVIS_VOICE=Kore\n"


def _audio(pcm, done):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=pcm, mime_type="audio/pcm"))]),
        turn_complete=done))


class FakeClient:
    def __init__(self):
        self.configs, self.texts = [], []

        @contextlib.asynccontextmanager
        async def connect(model, config):
            self.configs.append(config)
            outer = self

            class Session:
                async def send_realtime_input(self, text=None, **_):
                    outer.texts.append(text)

                async def receive(self):
                    for message in (_audio(b"ab", False), _audio(b"cd", True), _audio(b"zz", True)):
                        yield message
            yield Session()

        self.aio = SimpleNamespace(live=SimpleNamespace(connect=connect))


async def test_audition_collects_one_turn_of_audio_in_the_requested_voice():
    client = FakeClient()
    pcm = await audition(client, "m", "Puck")
    assert pcm == b"abcd"
    assert client.configs[0].speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"
    assert client.texts and client.texts[0]


async def test_run_audition_plays_each_voice_and_saves_the_pick(tmp_path):
    env = tmp_path / ".env"
    env.write_text("JARVIS_VOICE=Charon\n")
    played, printed = [], []

    async def fake_audition(client, model, voice):
        return voice.encode()

    chosen = await run_audition(None, "m", ["Puck", "Kore"], env_file=env, play=played.append,
                                ask=lambda prompt: "2", say=printed.append, audition_fn=fake_audition,
                                wait=lambda pcm: asyncio.sleep(0))
    assert chosen == "Kore"
    assert played == [b"Puck", b"Kore"]
    assert any("Puck" in line for line in printed)
    assert env.read_text() == "JARVIS_VOICE=Kore\n"


async def test_run_audition_can_pick_by_name_skip_or_survive_a_failed_voice(tmp_path):
    env = tmp_path / ".env"
    env.write_text("JARVIS_VOICE=Charon\n")

    async def flaky(client, model, voice):
        if voice == "Puck":
            raise RuntimeError("quota")
        return b"x"

    printed = []
    kwargs = dict(env_file=env, play=lambda pcm: None, say=printed.append, audition_fn=flaky,
                  wait=lambda pcm: asyncio.sleep(0))
    assert await run_audition(None, "m", ["Puck", "Kore"], ask=lambda p: "kore", **kwargs) == "Kore"
    assert any("quota" in line for line in printed)
    assert await run_audition(None, "m", ["Kore"], ask=lambda p: "", **kwargs) is None
    assert env.read_text() == "JARVIS_VOICE=Kore\n"


async def test_audition_can_save_through_a_callback_instead_of_dotenv(tmp_path):
    saved = []

    async def fake(client, model, voice):
        return b"\x00\x00"

    async def no_wait(pcm):
        return None

    env = tmp_path / ".env"
    chosen = await run_audition(None, "m", ["Puck", "Kore"], env_file=env, play=lambda pcm: None,
                                ask=lambda prompt: "2", say=lambda line: None, audition_fn=fake, wait=no_wait,
                                save=saved.append)
    assert chosen == "Kore" and saved == ["Kore"] and not env.exists()
