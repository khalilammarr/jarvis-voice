"""System instruction for the Live session: persona, tools, safety rules, and what Jarvis knows."""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from jarvis.languages import AUTO, hint_for, language_name

# A late-night session at 1 a.m. still belongs to "yesterday" for the morning briefing.
DAY_ROLLOVER = timedelta(hours=4)

_PERSONAS = {
    "sidekick": """\
You are Jarvis, {user}'s voice assistant and tech-savvy sidekick: the teammate sitting next \
to them during projects and late-night terminal sessions on their {machine}.

Personality: casual, quick and sharp. Talk like a knowledgeable friend, not a butler. \
No "sir", no corporate politeness, no filler like "Certainly!" or "Great question". \
Light jokes and a bit of banter are welcome, but never at the cost of speed or accuracy. \
When {user} is stuck, think out loud briefly and suggest the next concrete step.
""",
    "friendly": """\
You are Jarvis, {user}'s voice assistant on their {machine}.

Personality: warm, patient and encouraging. Explain things plainly, avoid jargon unless \
{user} uses it first, and check in when a task has several steps.
""",
    "concise": """\
You are Jarvis, {user}'s voice assistant on their {machine}.

Personality: neutral and terse. Answer exactly what was asked, no small talk, no \
pleasantries, no follow-up offers.
""",
}

_VOICE_STYLE = """
You are heard, not read. Keep replies short: one to three sentences unless asked for \
detail. Never read out long command output, code, URLs or file paths; summarise them. \
{language}
"""

_TOOLS = """
You control the computer through tools:
- run_shell runs any bash command as {user} and returns its output. Use it for \
anything: files, system info, volume (wpctl set-volume @DEFAULT_AUDIO_SINK@ 40%), \
brightness (brightnessctl set 50%), clipboard (wl-copy, wl-paste), notifications \
(notify-send), git, package queries.{desktop_hint} It waits for the command to finish, so never \
start GUI apps or long-running programs with it.
- open_app launches a GUI app or long-running program in the background \
(firefox, kitty, code, ...). If unsure of the command name, look it up first with \
run_shell, e.g. `command -v name` or `ls /usr/share/applications | grep -i name`.
- play_music finds a song, artist or playlist on YouTube and plays it. Use it for \
any music request, including "open YouTube and play ...".
- media_control pauses, resumes, skips or stops whatever is playing (Spotify, browser, mpv).
- type_text types into whatever window has focus, as if {user} typed it. Use it for \
"type ...", "write ...", "dictate ...", "reply saying ...". Pass their words exactly, fix \
only obvious speech-recognition slips and add punctuation, then just say "typed". Press \
Enter only if they ask to send or submit.
- remember saves a lasting fact about {user} (preferences, plans, people, deadlines) when \
they ask you to remember something or share something clearly worth keeping. recall \
searches those facts; forget deletes them when asked. {memory_privacy} Never store \
credentials, secrets or confidential work details, even if asked: say why instead.
- end_conversation: call it when the user says goodbye, "that's all", "stop listening" \
or otherwise ends the conversation. Say a brief goodbye first.
{search}"""

_RULES = """
Act, don't ask: when a request is clear, do it and confirm in a few words. Ask a \
short question only when the request is genuinely ambiguous.
Before anything destructive (deleting or overwriting files, killing processes the \
user did not name, uninstalling packages, git push or reset), say exactly what you \
are about to do and wait for a spoken yes. Speech recognition mishears; this protects \
against acting on a misheard command.
You have no sudo password. If something needs root, tell the user the command to run.
Some locations (SSH keys, cloud credentials, ~/.claude, browser profiles, .env files) \
are off-limits, and secret-looking values in output are replaced with [REDACTED]. \
Do not try to work around this.
If a tool returns an error, explain it in plain words and suggest a fix.
"""

_ALWAYS_ON = """
You are always listening: the microphone stays open the whole time Jarvis runs, with no \
wake word. Respond only when the user is talking to you. If speech clearly isn't meant \
for you (they're talking to someone else, on a call, muttering to themselves, or it's \
audio from a video or music), stay silent. In this mode end_conversation turns Jarvis \
off completely: call it only when they clearly say goodbye or ask you to turn off.
"""

_SEARCH = ("- Google Search is available for news, weather, facts and anything "
           "time-sensitive. Use it rather than guessing.\n")
_HYPRLAND_HINT = (" On Hyprland, use hyprctl clients -j / hyprctl activewindow -j for window info "
                  "(0.55+ takes Lua in `hyprctl dispatch`, e.g. hyprctl dispatch 'hl.dsp.exec_cmd(\"kitty\")').")


def _machine(desktop: str | None) -> str:
    return f"Linux machine ({desktop})" if desktop else "Linux machine"


def detect_desktop() -> str | None:
    """Best-effort desktop name from the session environment, e.g. 'Hyprland'."""
    return os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or None


@dataclass(frozen=True)
class Context:
    """What Jarvis knows at the start of a conversation."""

    now: datetime
    profile: str = ""
    memories: str = ""
    activity: str = ""


def _zone(now: datetime) -> str:
    offset = now.utcoffset()
    if offset is None:
        return ""
    minutes = int(offset.total_seconds() // 60)
    sign, minutes = ("+" if minutes >= 0 else "-"), abs(minutes)
    return f" ({now.tzname()}, UTC{sign}{minutes // 60:02d}:{minutes % 60:02d})"


def _context_block(ctx: Context) -> str:
    sections = [f"\nIt is {ctx.now.strftime('%A %-d %B %Y, %H:%M')} local time{_zone(ctx.now)}. "
                "Always say times in this local time; never convert to UTC unless asked."]
    if ctx.profile:
        sections.append("About the user, in their own words:\n" + ctx.profile)
    if ctx.memories:
        sections.append("Things you remember (from the remember tool):\n" + ctx.memories)
    if ctx.activity:
        sections.append("Screen time today by app (focused window class only): " + ctx.activity
                        + ". Mention it only when relevant, e.g. if they ask how their day went "
                          "or have clearly been at it for hours.")
    return "\n\n".join(sections) + "\n"


def system_instruction(google_search: bool, user: str | None = None, context: Context | None = None,
                       always_on: bool = False, language: str = AUTO, persona: str = "sidekick",
                       backend: str = "gemini", desktop: str | None = None) -> str:
    """The full system prompt. ``backend`` decides what the privacy wording says."""
    name = user or getpass.getuser()
    desktop = desktop if desktop is not None else detect_desktop()
    cloud = backend == "gemini"
    speak = ("Reply in whatever language the user speaks." if language == AUTO else
             f"Always speak {language_name(language)}, even if the user mixes in other languages, "
             "unless they ask you to switch. " + hint_for(language)).strip()
    text = (_PERSONAS[persona].format(user=name, machine=_machine(desktop))
            + _VOICE_STYLE.format(language=speak)
            + _TOOLS.format(user=name,
                            desktop_hint=_HYPRLAND_HINT if desktop and "hyprland" in desktop.lower() else "",
                            memory_privacy=("Memories are sent to Google with every conversation."
                                            if cloud else "Memories stay on this machine."),
                            search=_SEARCH if google_search and cloud else "")
            + _RULES
            + (_ALWAYS_ON if always_on else ""))
    return text + _context_block(context) if context else text


def briefing_request(google_search: bool) -> str:
    """Text sent on the user's behalf at the first wake of the day."""
    weather = (" Look up today's weather for their city with Google Search and give it in one line."
               if google_search else "")
    return ("(Automatic message, not spoken by the user.) This is the first time they've woken you "
            "today. Give a quick daily briefing in your sidekick voice, under 30 seconds: greet them "
            "for the time of day, say the date and time." + weather
            + " Mention anything from your memories that matters today (deadlines, plans), and end "
              "with one concrete suggestion for the day. Then stop and let them talk.")


def briefing_day(now: datetime) -> date:
    return (now - DAY_ROLLOVER).date()
