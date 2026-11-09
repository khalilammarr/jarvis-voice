# Privacy and safety

## What leaves your machine

| Backend | Sent off the machine |
|---|---|
| **Local** | Nothing for thinking. `play_music` looks up YouTube (that is the request you made). `jarvis setup` and first use download models. |
| **Gemini** | Microphone audio the whole time Jarvis runs in always-on mode, plus everything given to the model: your profile (`me.md`), memories, screen-time summary, and the output of every command it runs. |

Free-tier Gemini keys may be used by Google to improve its models. Do not use that backend for
anything confidential. Because it matters, Jarvis prints a reminder when it starts in Gemini mode.

Turn Jarvis off (say goodbye, or stop the service) for calls and meetings in always-on mode.

## Shell access

Jarvis can run any command as you. Layers around it, none of which is a sandbox:

| Layer | What it does | Off switch |
|---|---|---|
| Audit log | Every command goes to `~/.local/state/jarvis/commands.log` as JSON lines; output is never logged | none |
| Guard | Refuses `rm -rf` on `/`, `$HOME` or system directories, `mkfs`, `dd` or redirects onto disks, `wipefs`, fork bombs | `guard = false` |
| Path filter | Refuses commands that name SSH keys, cloud credentials, browser profiles, `.env` files and similar; extend with `deny_paths` | edit `DEFAULT_DENY_PATHS` |
| Secret scrubber | Replaces key-shaped strings in command output before the model sees it | none |
| Prompt rules | Jarvis states destructive actions and waits for a spoken "yes" | edit `jarvis/prompt.py` |

Speech recognition mishears and models misjudge. The guard is a seatbelt, not a vault.
If you work with confidential material, add those directories to `deny_paths`.

## Typing for you

`type_text` sends keystrokes to whatever window has focus. The text is passed to `wtype`, `xdotool` or
`ydotool` as a single argument, never through a shell, and Enter is pressed only when asked. The audit
log records the number of characters, not the text. With the Gemini backend the words you dictate are
heard by Google like everything else you say.

## Memory and activity

`remember` refuses secret-looking facts. Day tracking records only the focused window **class**
(`kitty`, `firefox`), never titles or URLs, and keeps 14 days.
