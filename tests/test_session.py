import io
from pathlib import Path

from google.genai import types

from jarvis.config import Config
from jarvis.prompt import system_instruction
from jarvis.session import live_config
from jarvis.shell import ShellPolicy
from jarvis.ui import Transcript


def _cfg(**overrides):
    base = dict(api_key="k", model=None, voice="Charon", wake_model="hey_jarvis",
                wake_threshold=0.5, idle_timeout_s=20, barge_in=False, google_search=True,
                input_device=None, output_device=None,
                shell=ShellPolicy(True, (), 60, 4000, Path("/tmp/x"), Path("/tmp")))
    return Config(**{**base, **overrides})


def test_live_config_requests_audio_with_tools_and_transcripts():
    config = live_config(_cfg())
    assert config.response_modalities == [types.Modality.AUDIO]
    assert config.tools[0].function_declarations[0].name == "run_shell"
    assert config.tools[1].google_search is not None
    assert config.input_audio_transcription is not None
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"


def test_google_search_can_be_disabled():
    config = live_config(_cfg(google_search=False))
    assert len(config.tools) == 1
    instruction = config.system_instruction
    text = instruction if isinstance(instruction, str) else instruction.parts[0].text
    assert "Google Search" not in text


def test_system_instruction_mentions_user_and_safety_rules():
    text = system_instruction(google_search=True, user="alex", desktop="Hyprland")
    assert "alex" in text and "Hyprland" in text and "spoken yes" in text


def test_transcript_groups_streamed_chunks_by_speaker():
    out = io.StringIO()
    t = Transcript(out)
    t.say("you", "open ")
    t.say("you", "firefox")
    t.tool("open_app", {"command": "firefox"})
    t.say("jarvis", "Done.")
    t.end_turn()
    text = out.getvalue()
    assert "open firefox\n" in text and "⚙ open_app  firefox" in text and "Done.\n" in text


def test_always_on_sessions_resume_compress_and_ignore_side_talk():
    config = live_config(_cfg(wake_word=False), handle="h-123")
    assert config.session_resumption.handle == "h-123"
    assert config.context_window_compression.sliding_window is not None
    assert config.proactivity.proactive_audio is True


def test_wake_word_sessions_stay_plain():
    config = live_config(_cfg(wake_word=True))
    assert config.session_resumption is None and config.proactivity is None
