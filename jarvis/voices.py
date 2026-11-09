"""`jarvis --voices`: hear each Gemini prebuilt voice say a line, then save the pick to .env."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from google.genai import types

from jarvis.config import SPEAKER_RATE
from jarvis.fileio import write_atomic

# Gemini Live / TTS prebuilt voices (Google's published list, 2026).
VOICES: tuple[str, ...] = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
    "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)

AUDITION_LINE = ("Hey, I'm Jarvis. Your build finished with no errors, which is suspicious. "
                 "Want me to open the editor, or put some music on?")
_INSTRUCTION = ("You are a voice actor auditioning for a voice-assistant role. When given a line, "
                "say exactly that line, in a friendly, quick, confident tone. Say nothing else.")
_AUDITION_TIMEOUT_S = 30.0


def parse_voices(spec: str | None) -> list[str]:
    """'puck,kore' -> ['Puck', 'Kore']; None -> every voice. Unknown names raise ValueError."""
    if not spec:
        return list(VOICES)
    by_lower = {v.lower(): v for v in VOICES}
    wanted = [part.strip() for part in spec.split(",") if part.strip()]
    unknown = [name for name in wanted if name.lower() not in by_lower]
    if unknown:
        raise ValueError(f"Unknown voice(s): {', '.join(unknown)}. Known: {', '.join(VOICES)}")
    return [by_lower[name.lower()] for name in wanted]


def set_env_value(env_file: Path, key: str, value: str) -> None:
    """Set KEY=value in a dotenv file, in place if present, keeping every other line and the mode."""
    try:
        text = env_file.read_text(encoding="utf-8")
        mode = env_file.stat().st_mode & 0o777
    except FileNotFoundError:
        text, mode = "", 0o600
    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(lambda _: line, text, count=1)
    else:
        text = (text if not text or text.endswith("\n") else text + "\n") + line + "\n"
    write_atomic(env_file, text, mode=mode)


def _config(voice: str) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=_INSTRUCTION,
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
    )


async def audition(client, model: str, voice: str) -> bytes:
    """One Live session: ask ``voice`` to read the audition line, return its PCM (24 kHz int16)."""
    pcm = bytearray()

    async def _run() -> None:
        async with client.aio.live.connect(model=model, config=_config(voice)) as session:
            await session.send_realtime_input(text=f"Your line: {AUDITION_LINE}")
            async for message in session.receive():
                content = message.server_content
                if not content:
                    continue
                for part in (content.model_turn.parts or []) if content.model_turn else []:
                    if part.inline_data and part.inline_data.data:
                        pcm.extend(part.inline_data.data)
                if content.turn_complete:
                    return

    await asyncio.wait_for(_run(), _AUDITION_TIMEOUT_S)
    return bytes(pcm)


def _pick(answer: str, voices: Sequence[str]) -> str | None:
    answer = answer.strip()
    if answer.isdigit() and 1 <= int(answer) <= len(voices):
        return voices[int(answer) - 1]
    try:
        return parse_voices(answer)[0] if answer else None
    except ValueError:
        return None


async def run_audition(client, model: str, voices: Sequence[str], env_file: Path, *,
                       play: Callable[[bytes], None], ask: Callable[[str], str] = input,
                       say: Callable[[str], None] = print,
                       audition_fn: Callable[..., Awaitable[bytes]] = audition,
                       wait: Callable[[bytes], Awaitable[None]] | None = None,
                       save: Callable[[str], None] | None = None) -> str | None:
    """Play every voice, then let the user pick one by number or name. Returns the pick."""
    wait = wait or (lambda pcm: asyncio.sleep(len(pcm) / (2 * SPEAKER_RATE) + 0.6))
    for number, voice in enumerate(voices, 1):
        say(f"[{number}/{len(voices)}] {voice}")
        try:
            pcm = await audition_fn(client, model, voice)
        except Exception as exc:  # quota, network: skip this voice, keep auditioning
            say(f"   could not play {voice}: {type(exc).__name__}: {exc}")
            continue
        play(pcm)
        await wait(pcm)

    current = os.environ.get("JARVIS_VOICE", "Charon")
    choice = _pick(ask(f"\nPick a voice (number or name, Enter keeps {current}): "), voices)
    if choice is None:
        say(f"Keeping {current}.")
        return None
    if save is not None:
        save(choice)
        say(f"Saved voice = {choice!r} to your config. Restart Jarvis to use it.")
    else:
        set_env_value(env_file, "JARVIS_VOICE", choice)
        say(f"Saved JARVIS_VOICE={choice} to {env_file}. Restart Jarvis to use it.")
    return choice
