"""LocalConversation with fake STT / LLM / TTS / mic / speaker: no models, no audio hardware."""

import asyncio
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from jarvis.backends.local.conversation import LocalConversation
from jarvis.backends.local.errors import LocalBackendError
from jarvis.backends.local.llm import ToolCall
from jarvis.config import Config, LocalConfig
from jarvis.prompt import Context
from jarvis.shell import ShellPolicy
from jarvis.ui import Transcript
import io


def _cfg(**overrides):
    base = dict(api_key="", model=None, voice="Charon", wake_model="hey_jarvis", wake_threshold=0.5,
                idle_timeout_s=0.5, barge_in=False, google_search=False, input_device=None, output_device=None,
                shell=ShellPolicy(True, (), 60, 4000, Path("/tmp/x"), Path("/tmp")),
                backend="local", wake_word=False, language="fr", local=LocalConfig())
    return Config(**{**base, **overrides})


class FakeMic:
    def __init__(self):
        self.q = asyncio.Queue()

    async def read(self):
        return await self.q.get()

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()


class FakeSpeaker:
    def __init__(self):
        self.played, self.flushed, self._busy = [], 0, False

    def play(self, pcm):
        self.played.append(pcm)

    def flush(self):
        self.flushed += 1

    @property
    def busy(self):
        return self._busy


class ScriptedEndpointer:
    """Emits an utterance on the frames listed in `at`; in_speech toggled by the test."""

    def __init__(self, at=(0,)):
        self.at, self.i, self.in_speech, self.resets = set(at), -1, False, 0

    def feed(self, frame):
        self.i += 1
        return np.ones(1600, dtype=np.int16) if self.i in self.at else None

    def reset(self):
        self.resets += 1


class FakeSTT:
    def __init__(self, texts):
        self.texts, self.languages = list(texts), []

    async def transcribe(self, audio, language):
        self.languages.append(language)
        return self.texts.pop(0) if self.texts else ""


class FakeTTS:
    def __init__(self):
        self.said = []

    async def synthesize(self, text):
        self.said.append(text)
        return text.encode()


class FakeLLM:
    """Each call to stream() plays the next scripted reply: a list of str deltas / ToolCall lists."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def stream(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        for event in self.replies.pop(0):
            if isinstance(event, Exception):
                raise event
            yield event


class FakeTools:
    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}


def build(replies, texts=("hello",), **kw):
    mic, speaker, tools = FakeMic(), FakeSpeaker(), FakeTools()
    runtime = NS(stt=FakeSTT(texts), llm=FakeLLM(replies), tts=FakeTTS(),
                 new_endpointer=lambda: kw.pop("endpointer", ScriptedEndpointer()))
    out = io.StringIO()
    convo = LocalConversation(runtime, "m", kw.pop("cfg", _cfg()), mic, speaker, tools, Transcript(out),
                              context=kw.pop("context", None), opening=kw.pop("opening", None))
    return convo, mic, speaker, tools, runtime, out


async def run_until(convo, mic, frames=3, settle=0.15, timeout=3.0):
    for _ in range(frames):
        mic.q.put_nowait(np.zeros(1280, dtype=np.int16))
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(settle)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_utterance_is_transcribed_answered_and_spoken():
    convo, mic, speaker, tools, rt, out = build([["Bonjour. Ça va ?"]])
    await run_until(convo, mic)
    assert rt.llm.calls[0][-1] == {"role": "user", "content": "hello"}
    assert rt.tts.said == ["Bonjour.", "Ça va ?"]
    assert speaker.played == [b"Bonjour.", "Ça va ?".encode()]
    assert "hello" in out.getvalue() and "Bonjour." in out.getvalue()


async def test_whisper_gets_the_configured_language_or_none_for_auto():
    convo, mic, *_rest, rt, _ = build([["ok."]])
    await run_until(convo, mic)
    assert rt.stt.languages == ["fr"]
    convo, mic, *_rest, rt, _ = build([["ok."]], cfg=_cfg(language="auto"))
    await run_until(convo, mic)
    assert rt.stt.languages == [None]


async def test_system_prompt_names_the_language_and_has_no_google_wording():
    convo, mic, *_rest, rt, _ = build([["ok."]], context=Context(now=__import__("datetime").datetime(2026, 1, 1, 9, 0)))
    await run_until(convo, mic)
    system = rt.llm.calls[0][0]
    assert system["role"] == "system" and "French" in system["content"]
    assert "Google" not in system["content"]


async def test_empty_transcription_is_ignored():
    convo, mic, speaker, tools, rt, _ = build([["never used."]], texts=("",))
    await run_until(convo, mic)
    assert rt.llm.calls == [] and speaker.played == []


async def test_tool_call_runs_the_tool_then_the_model_continues():
    replies = [[[ToolCall("c1", "run_shell", {"command": "date"})]], ["It is noon."]]
    convo, mic, speaker, tools, rt, _ = build(replies)
    await run_until(convo, mic)
    assert tools.calls == [("run_shell", {"command": "date"})]
    second = rt.llm.calls[1]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1" and '"ok": true' in second[-1]["content"]
    assert rt.tts.said == ["It is noon."]


async def test_bad_tool_arguments_are_reported_back_to_the_model():
    bad = ToolCall("c1", "run_shell", {}, error="arguments were not valid JSON (oops)")
    convo, mic, speaker, tools, rt, _ = build([[[bad]], ["Sorry."]])
    await run_until(convo, mic)
    assert tools.calls == []
    assert "not valid JSON" in rt.llm.calls[1][-1]["content"]


async def test_end_conversation_ends_with_goodbye_after_speaking():
    replies = [["Bye for now. ", [ToolCall("c1", "end_conversation", {})]], []]
    convo, mic, speaker, tools, rt, _ = build(replies)
    for _ in range(3):
        mic.q.put_nowait(np.zeros(1280, dtype=np.int16))
    assert await asyncio.wait_for(convo.run(), 5) == "goodbye"
    assert tools.calls == []  # handled internally, not passed to the shell tools
    assert rt.tts.said[0] == "Bye for now."


async def test_server_down_is_reported_and_the_conversation_survives():
    convo, mic, speaker, tools, rt, out = build([[LocalBackendError("Can't reach the server")], ["Back."]],
                                                texts=("one", "two"), endpointer=ScriptedEndpointer(at=(0, 1)))
    mic.q.put_nowait(np.zeros(1280, dtype=np.int16))
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(0.15)  # first turn fails and is reported
    mic.q.put_nowait(np.zeros(1280, dtype=np.int16))
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert "Can't reach the server" in out.getvalue()
    assert rt.llm.calls[1][-1] == {"role": "user", "content": "two"}
    assert {"role": "user", "content": "one"} not in rt.llm.calls[1]  # the unanswered turn was dropped


async def test_half_duplex_ignores_the_mic_while_jarvis_speaks():
    ep = ScriptedEndpointer(at=(0,))
    convo, mic, speaker, tools, rt, _ = build([["Hi."]], endpointer=ep)
    speaker._busy = True
    await run_until(convo, mic)
    assert rt.llm.calls == [] and ep.i == -1 and ep.resets > 0


async def test_opening_message_makes_jarvis_speak_first():
    convo, mic, speaker, tools, rt, _ = build([["Good morning."]], opening="(briefing please)")
    await run_until(convo, mic, frames=0)
    assert rt.llm.calls[0][-1] == {"role": "user", "content": "(briefing please)"}
    assert rt.tts.said == ["Good morning."]


async def test_history_is_trimmed_but_keeps_the_system_prompt():
    convo, mic, speaker, tools, rt, _ = build([["ok."]] * 40, texts=[f"q{i}" for i in range(40)],
                                              endpointer=ScriptedEndpointer(at=range(0, 80, 2)))
    await run_until(convo, mic, frames=80, settle=0.6)
    last = rt.llm.calls[-1]
    assert last[0]["role"] == "system" and last[1]["role"] == "user" and len(last) <= 30


async def test_barge_in_flushes_speech_and_cancels_the_reply():
    ep = ScriptedEndpointer(at=(0,))
    gate = asyncio.Event()

    class SlowLLM(FakeLLM):
        async def stream(self, messages, tools):
            yield "Long answer. "
            await gate.wait()
            yield "never."

    convo, mic, speaker, tools, rt, _ = build([], cfg=_cfg(barge_in=True), endpointer=ep)
    rt.llm = SlowLLM([])
    for _ in range(2):
        mic.q.put_nowait(np.zeros(1280, dtype=np.int16))
    task = asyncio.create_task(convo.run())
    await asyncio.sleep(0.15)
    ep.in_speech = True
    mic.q.put_nowait(np.zeros(1280, dtype=np.int16))
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert speaker.flushed >= 1 and "never." not in rt.tts.said


async def test_whisper_gets_the_base_language_code_for_a_dialect():
    convo, mic, *_rest, rt, _ = build([["ok."]], cfg=_cfg(language="ar-tn"))
    await run_until(convo, mic)
    assert rt.stt.languages == ["ar"]
