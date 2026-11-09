"""One spoken conversation with Gemini Live, from wake word to goodbye or silence."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from jarvis.audio import Microphone, Speaker
from jarvis.config import MIC_RATE, Config
from jarvis.prompt import Context, system_instruction
from jarvis.tools import ToolRunner, function_declarations
from jarvis.ui import Transcript

log = logging.getLogger(__name__)

END_GRACE_S = 1.0  # after end_conversation, let any trailing goodbye audio arrive


@dataclass
class ResumeState:
    """Latest session-resumption handle, shared across reconnects in always-on mode."""

    handle: str | None = None


def _always_on_options(handle: str | None) -> dict:
    """An always-open session outlives Gemini's connection and context limits by resuming
    (same conversation after a reconnect) and sliding the context window. Proactive audio
    lets the model stay quiet when the speech it hears isn't meant for it."""
    return dict(
        session_resumption=types.SessionResumptionConfig(handle=handle),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        proactivity=types.ProactivityConfig(proactive_audio=True),
    )


def live_config(cfg: Config, context: Context | None = None, handle: str | None = None) -> types.LiveConnectConfig:
    tools = [types.Tool(function_declarations=function_declarations())]
    if cfg.google_search:
        tools.append(types.Tool(google_search=types.GoogleSearch()))
    extra = {} if cfg.wake_word else _always_on_options(handle)
    return types.LiveConnectConfig(
        **extra,
        response_modalities=[types.Modality.AUDIO],
        system_instruction=system_instruction(cfg.google_search, context=context, always_on=not cfg.wake_word),
        tools=tools,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=cfg.voice))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


class Conversation:
    def __init__(self, client: genai.Client, model: str, cfg: Config, mic: Microphone,
                 speaker: Speaker, tools: ToolRunner, transcript: Transcript,
                 context: Context | None = None, opening: str | None = None,
                 resume: ResumeState | None = None):
        self._client, self._model, self._cfg = client, model, cfg
        self._context, self._opening, self._resume = context, opening, resume
        self._mic, self._speaker, self._tools, self._transcript = mic, speaker, tools, transcript
        self._last_activity = time.monotonic()
        self._end_requested = False
        self._pending_tools = 0
        self._tool_tasks: set[asyncio.Task] = set()

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    async def run(self) -> str:
        """Returns why the conversation ended: "goodbye" or "idle"."""
        config = live_config(self._cfg, self._context, self._resume.handle if self._resume else None)
        async with self._client.aio.live.connect(model=self._model, config=config) as session:
            if self._opening:  # e.g. the daily briefing: Jarvis speaks first
                await session.send_realtime_input(text=self._opening)
            tasks = {
                asyncio.create_task(self._send_microphone(session), name="microphone"),
                asyncio.create_task(self._receive(session), name="receive"),
                asyncio.create_task(self._watchdog(), name="watchdog"),
            }
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks | self._tool_tasks:
                    task.cancel()
                await asyncio.gather(*tasks, *self._tool_tasks, return_exceptions=True)
            # An error from any task outranks the watchdog's verdict, whatever finished first.
            failures = [t.exception() for t in tasks if not t.cancelled() and t.exception()]
            if failures:
                raise failures[0]
            watchdog = next(t for t in tasks if t.get_name() == "watchdog")
            return "closed" if watchdog.cancelled() else watchdog.result()

    async def _send_microphone(self, session) -> None:
        mime = f"audio/pcm;rate={MIC_RATE}"
        while True:
            frame = await self._mic.read()
            if not self._cfg.barge_in and self._speaker.busy:
                continue  # half-duplex: laptop speakers would otherwise feed Jarvis its own voice
            await session.send_realtime_input(audio=types.Blob(data=frame.tobytes(), mime_type=mime))

    async def _receive(self, session) -> None:
        while True:
            received = 0
            async for message in session.receive():  # yields one model turn, then returns
                received += 1
                self._handle(session, message)
            if not received:
                raise ConnectionError("The Live session was closed by the server.")

    def _handle(self, session, message: types.LiveServerMessage) -> None:
        if message.tool_call and message.tool_call.function_calls:
            task = asyncio.create_task(self._run_tools(session, message.tool_call.function_calls))
            self._tool_tasks.add(task)
            task.add_done_callback(self._tool_tasks.discard)

        content = message.server_content
        if content:
            if content.interrupted:
                self._speaker.flush()
            if content.model_turn:
                for part in content.model_turn.parts or []:
                    if part.inline_data and part.inline_data.data:
                        self._speaker.play(part.inline_data.data)
                        self._touch()
            if content.input_transcription and content.input_transcription.text:
                self._transcript.say("you", content.input_transcription.text)
                self._touch()
            if content.output_transcription and content.output_transcription.text:
                self._transcript.say("jarvis", content.output_transcription.text)
            if content.turn_complete:
                self._transcript.end_turn()

        update = message.session_resumption_update
        if update and update.resumable and update.new_handle and self._resume is not None:
            self._resume.handle = update.new_handle

        if message.go_away:
            log.warning("server will close the session in %s", message.go_away.time_left)

    async def _run_tools(self, session, calls: list[types.FunctionCall]) -> None:
        self._pending_tools += 1
        try:
            responses = []
            for call in calls:
                args = dict(call.args or {})
                self._transcript.tool(call.name, args)
                if call.name == "end_conversation":
                    self._end_requested = True
                    result = {"status": "ending"}
                else:
                    result = await self._tools.call(call.name, args)
                responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
            await session.send_tool_response(function_responses=responses)
        finally:
            self._pending_tools -= 1
            self._touch()

    async def _watchdog(self) -> str:
        while True:
            await asyncio.sleep(0.25)
            if self._speaker.busy or self._pending_tools:
                self._touch()
                continue
            idle = time.monotonic() - self._last_activity
            if self._end_requested and idle > END_GRACE_S:
                return "goodbye"
            if self._cfg.wake_word and idle > self._cfg.idle_timeout_s:
                return "idle"
