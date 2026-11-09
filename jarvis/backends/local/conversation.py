"""One spoken conversation, fully on this machine: mic -> endpointer -> Whisper -> LLM -> Piper."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from jarvis.backends.local.errors import LocalBackendError
from jarvis.backends.local.speech import SentenceSplitter
from jarvis.languages import AUTO
from jarvis.prompt import Context, system_instruction
from jarvis.tools import openai_tools

log = logging.getLogger(__name__)

END_GRACE_S = 1.0       # after end_conversation, let the goodbye finish playing
MAX_TOOL_ROUNDS = 6     # tool -> model -> tool ... before giving up on a runaway model
MAX_HISTORY = 24        # messages kept besides the system prompt


class LocalConversation:
    """Same surface as the Gemini ``Conversation``: construct it, ``await run()`` for the end reason.

    ``runtime`` provides ``stt``, ``llm``, ``tts`` and ``new_endpointer()``.
    """

    def __init__(self, runtime, model, cfg, mic, speaker, tools, transcript,
                 context: Context | None = None, opening: str | None = None, resume=None):
        self._rt, self._cfg = runtime, cfg
        self._mic, self._speaker, self._tools, self._transcript = mic, speaker, tools, transcript
        self._context, self._opening = context, opening
        self._language = None if cfg.language == AUTO else cfg.language
        self._history: list[dict] = []
        self._turn: asyncio.Task | None = None
        self._endpointer = None
        self._last_activity = time.monotonic()
        self._end_requested = False

    # -- lifecycle ---------------------------------------------------------

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    def _turn_running(self) -> bool:
        return self._turn is not None and not self._turn.done()

    async def run(self) -> str:
        """Returns why the conversation ended: "goodbye" or "idle"."""
        self._endpointer = self._rt.new_endpointer()
        system = system_instruction(False, context=self._context, always_on=not self._cfg.wake_word,
                                    language=self._cfg.language, persona=self._cfg.persona, backend="local")
        self._history = [{"role": "system", "content": system}]
        tasks = {asyncio.create_task(self._listen(), name="listen"),
                 asyncio.create_task(self._watchdog(), name="watchdog")}
        if self._opening:  # e.g. the daily briefing: Jarvis speaks first
            self._turn = asyncio.create_task(self._reply(self._opening), name="turn")
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks | ({self._turn} if self._turn else set()):
                task.cancel()
            await asyncio.gather(*tasks, *([self._turn] if self._turn else []), return_exceptions=True)
            self._speaker.flush()
        failures = [t.exception() for t in tasks if not t.cancelled() and t.exception()]
        if failures:
            raise failures[0]
        watchdog = next(t for t in tasks if t.get_name() == "watchdog")
        return "closed" if watchdog.cancelled() else watchdog.result()

    # -- listening ---------------------------------------------------------

    async def _listen(self) -> None:
        ep, cfg = self._endpointer, self._cfg
        while True:
            frame = await self._mic.read()
            busy = self._turn_running() or self._speaker.busy
            if busy and not cfg.barge_in:
                ep.reset()  # half-duplex: laptop speakers would otherwise feed Jarvis its own voice
                continue
            utterance = ep.feed(frame)
            if ep.in_speech:
                self._touch()
                if busy:
                    await self._interrupt()
            if utterance is not None:
                await self._begin_turn(utterance)

    async def _interrupt(self) -> None:
        if self._turn_running():
            self._turn.cancel()
            await asyncio.gather(self._turn, return_exceptions=True)
        self._speaker.flush()

    async def _begin_turn(self, audio) -> None:
        if self._turn_running():
            await self._interrupt()
        self._turn = asyncio.create_task(self._respond(audio), name="turn")

    # -- one turn ----------------------------------------------------------

    async def _respond(self, audio) -> None:
        text = (await self._rt.stt.transcribe(audio, self._language)).strip()
        if not text:
            return
        self._transcript.say("you", text)
        self._transcript.end_turn()
        self._touch()
        await self._reply(text)

    async def _reply(self, user_text: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._trim()
        mark = len(self._history) - 1
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                calls = await self._stream_round()
                if not calls or await self._run_tools(calls):
                    return
            self._transcript.error("The model kept calling tools without answering; giving up on this turn.")
        except LocalBackendError as exc:
            del self._history[mark:]  # the unanswered turn must not haunt the next one
            self._transcript.error(str(exc))
        finally:
            self._transcript.end_turn()
            self._touch()

    async def _stream_round(self) -> list:
        splitter, spoken, calls = SentenceSplitter(), [], []
        async for event in self._rt.llm.stream(self._history, openai_tools()):
            if isinstance(event, str):
                for sentence in splitter.feed(event):
                    spoken.append(sentence)
                    await self._say(sentence)
            else:
                calls = event
        tail = splitter.flush()
        if tail:
            spoken.append(tail)
            await self._say(tail)
        message: dict = {"role": "assistant", "content": " ".join(spoken) or None}
        if calls:
            message["tool_calls"] = [{"id": c.id, "type": "function",
                                      "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                                     for c in calls]
        if spoken or calls:
            self._history.append(message)
        return calls

    async def _say(self, sentence: str) -> None:
        self._transcript.say("jarvis", sentence + " ")
        self._speaker.play(await self._rt.tts.synthesize(sentence))
        self._touch()

    async def _run_tools(self, calls) -> bool:
        """Run the model's tool calls. True when the conversation is over and no reply is needed."""
        for call in calls:
            self._transcript.tool(call.name, call.arguments)
            if call.error:
                result = {"error": f"Could not run {call.name}: {call.error}. Try again with valid arguments."}
            elif call.name == "end_conversation":
                self._end_requested = True
                result = {"status": "ending"}
            else:
                result = await self._tools.call(call.name, call.arguments)
            self._history.append({"role": "tool", "tool_call_id": call.id,
                                  "content": json.dumps(result, ensure_ascii=False)})
        return self._end_requested and all(c.name == "end_conversation" for c in calls)

    def _trim(self) -> None:
        """Drop the oldest messages, always resuming at a user message so tool pairs stay whole."""
        body = self._history[1:]
        if len(body) <= MAX_HISTORY:
            return
        body = body[len(body) - MAX_HISTORY:]
        while body and body[0]["role"] != "user":
            body.pop(0)
        self._history = [self._history[0], *body]

    # -- idle / goodbye ----------------------------------------------------

    async def _watchdog(self) -> str:
        while True:
            await asyncio.sleep(0.25)
            if self._turn is not None and self._turn.done() and not self._turn.cancelled():
                failure = self._turn.exception()
                if failure:
                    raise failure
            if self._speaker.busy or self._turn_running():
                self._touch()
                continue
            idle = time.monotonic() - self._last_activity
            if self._end_requested and idle > END_GRACE_S:
                return "goodbye"
            if self._cfg.wake_word and idle > self._cfg.idle_timeout_s:
                return "idle"
