"""Conversation loop against a fake Gemini Live session (no network, no audio hardware)."""

import asyncio
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from google.genai import types

from jarvis import session as session_mod
from jarvis.config import Config
from jarvis.session import Conversation
from jarvis.shell import ShellPolicy
from jarvis.ui import Transcript


def _cfg(**overrides):
    base = dict(api_key="k", model=None, voice="Charon", wake_model="hey_jarvis",
                wake_threshold=0.5, idle_timeout_s=0.4, barge_in=False, google_search=False,
                input_device=None, output_device=None,
                shell=ShellPolicy(True, (), 60, 4000, Path("/tmp/x"), Path("/tmp")))
    return Config(**{**base, **overrides})


def audio_msg(pcm=b"\x01\x02", turn_complete=True):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
            data=pcm, mime_type="audio/pcm;rate=24000"))]),
        turn_complete=turn_complete))


def tool_msg(name, args=None, call_id="call-1"):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id=call_id, name=name, args=args or {})]))


def transcript_msg(user=None, model=None, turn_complete=False):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        input_transcription=types.Transcription(text=user) if user else None,
        output_transcription=types.Transcription(text=model) if model else None,
        turn_complete=turn_complete))


class FakeSession:
    """`turns` is a list of message lists; receive() yields one list per call, then
    blocks forever once they run out (like a live socket with nothing to say)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.sent_audio, self.sent_text, self.tool_responses = [], [], []

    async def send_realtime_input(self, audio=None, text=None):
        if text is not None:
            self.sent_text.append(text)
        else:
            self.sent_audio.append(audio)

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)

    async def receive(self):
        if not self._turns:
            await asyncio.sleep(3600)
        for message in self._turns.pop(0):
            await asyncio.sleep(0)
            yield message


class FakeClient:
    def __init__(self, session):
        self.session = session

        @contextlib.asynccontextmanager
        async def connect(model, config):
            self.connected = (model, config)
            yield session

        self.aio = SimpleNamespace(live=SimpleNamespace(connect=connect))


class FakeMic:
    async def read(self):
        await asyncio.sleep(0.01)
        return np.zeros(1280, dtype=np.int16)


class FakeSpeaker:
    def __init__(self):
        self.played, self.flushes, self.busy = [], 0, False

    def play(self, pcm):
        self.played.append(pcm)

    def flush(self):
        self.flushes += 1


class FakeTools:
    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"output": f"ran {name}"}


@pytest.fixture(autouse=True)
def fast_grace(monkeypatch):
    monkeypatch.setattr(session_mod, "END_GRACE_S", 0.05)


def _conversation(turns, opening=None, context=None, **cfg):
    fake = FakeSession(turns)
    speaker, tools, out = FakeSpeaker(), FakeTools(), io.StringIO()
    convo = Conversation(FakeClient(fake), "models/test-live", _cfg(**cfg), FakeMic(),
                         speaker, tools, Transcript(out), context=context, opening=opening)
    return convo, fake, speaker, tools, out


async def test_model_audio_is_played_and_silence_ends_the_conversation():
    convo, _, speaker, _, _ = _conversation([[audio_msg(b"\xaa\xbb")]])
    assert await asyncio.wait_for(convo.run(), 5) == "idle"
    assert speaker.played == [b"\xaa\xbb"]


async def test_interruption_flushes_queued_speech():
    interrupted = types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))
    convo, _, speaker, _, _ = _conversation([[audio_msg(turn_complete=False), interrupted]])
    await asyncio.wait_for(convo.run(), 5)
    assert speaker.flushes == 1


async def test_tool_calls_run_and_reply_with_matching_id():
    convo, fake, _, tools, out = _conversation([[tool_msg("run_shell", {"command": "uptime"}, "abc")]])
    await asyncio.wait_for(convo.run(), 5)
    assert tools.calls == [("run_shell", {"command": "uptime"})]
    [response] = fake.tool_responses[0]
    assert (response.id, response.name, response.response) == ("abc", "run_shell", {"output": "ran run_shell"})
    assert "⚙ run_shell  uptime" in out.getvalue()


async def test_end_conversation_says_goodbye_without_calling_a_tool():
    convo, fake, _, tools, _ = _conversation([[tool_msg("end_conversation")]], idle_timeout_s=30)
    assert await asyncio.wait_for(convo.run(), 5) == "goodbye"
    assert tools.calls == []
    assert fake.tool_responses[0][0].response == {"status": "ending"}


async def test_server_closing_the_session_raises():
    convo, *_ = _conversation([[]], idle_timeout_s=30)
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(convo.run(), 5)


async def test_half_duplex_mutes_the_mic_while_jarvis_speaks():
    convo, fake, speaker, _, _ = _conversation([], idle_timeout_s=0.3)
    speaker.busy = True
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(0.15)
    assert fake.sent_audio == []
    speaker.busy = False
    await asyncio.wait_for(task, 5)
    assert fake.sent_audio and fake.sent_audio[0].mime_type == "audio/pcm;rate=16000"


async def test_barge_in_keeps_the_mic_open_while_jarvis_speaks():
    convo, fake, speaker, _, _ = _conversation([], idle_timeout_s=0.3, barge_in=True)
    speaker.busy = True
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(0.15)
    assert fake.sent_audio
    speaker.busy = False
    await asyncio.wait_for(task, 5)


async def test_transcripts_are_shown_and_user_speech_counts_as_activity():
    turns = [[transcript_msg(user="what time is it"), transcript_msg(model="It's noon.", turn_complete=True)]]
    convo, _, _, _, out = _conversation(turns)
    await asyncio.wait_for(convo.run(), 5)
    text = out.getvalue()
    assert "what time is it" in text and "It's noon." in text


async def test_task_errors_win_over_a_simultaneous_idle_result(monkeypatch):
    convo, *_ = _conversation([])
    async def boom(session):
        raise RuntimeError("mic died")
    async def idle():
        return "idle"
    monkeypatch.setattr(convo, "_send_microphone", boom)
    monkeypatch.setattr(convo, "_watchdog", idle)
    for _ in range(20):  # both finish on the first loop tick; order must not matter
        with pytest.raises(RuntimeError, match="mic died"):
            await asyncio.wait_for(convo.run(), 5)


async def test_opening_text_is_sent_first_so_jarvis_speaks_before_the_user():
    convo, fake, *_ = _conversation([], opening="Give the briefing.", idle_timeout_s=0.2)
    await asyncio.wait_for(convo.run(), 5)
    assert fake.sent_text == ["Give the briefing."]


async def test_without_opening_nothing_is_said_on_the_users_behalf():
    convo, fake, *_ = _conversation([], idle_timeout_s=0.2)
    await asyncio.wait_for(convo.run(), 5)
    assert fake.sent_text == []


async def test_context_reaches_the_system_instruction():
    from datetime import datetime
    from jarvis.prompt import Context
    convo, fake, *_ = _conversation([], context=Context(now=datetime(2026, 11, 7, 9, 0), profile="Handle: sam"),
                                    idle_timeout_s=0.2)
    client = convo._client
    await asyncio.wait_for(convo.run(), 5)
    instruction = client.connected[1].system_instruction
    text = instruction if isinstance(instruction, str) else instruction.parts[0].text
    assert "Handle: sam" in text


async def test_always_on_conversation_never_ends_from_silence():
    convo, *_ = _conversation([], idle_timeout_s=0.1, wake_word=False)
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(0.6)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_always_on_goodbye_still_ends():
    convo, *_ = _conversation([[tool_msg("end_conversation")]], wake_word=False)
    assert await asyncio.wait_for(convo.run(), 5) == "goodbye"


async def test_resumption_handles_are_remembered_and_reused():
    from jarvis.session import ResumeState
    update = types.LiveServerMessage(session_resumption_update=types.LiveServerSessionResumptionUpdate(
        new_handle="h-new", resumable=True))
    not_resumable = types.LiveServerMessage(session_resumption_update=types.LiveServerSessionResumptionUpdate(
        new_handle="", resumable=False))
    state = ResumeState(handle="h-old")
    fake = FakeSession([[update, not_resumable]])
    convo = Conversation(FakeClient(fake), "m", _cfg(wake_word=False, idle_timeout_s=0.2), FakeMic(),
                         FakeSpeaker(), FakeTools(), Transcript(io.StringIO()), resume=state)
    client = convo._client
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert client.connected[1].session_resumption.handle == "h-old"
    assert state.handle == "h-new"  # the non-resumable update did not wipe it
