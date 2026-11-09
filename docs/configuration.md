# Configuration

Sources, highest priority first: **environment variables**, `~/.config/jarvis/secrets.env`
(API key), a project-local `.env` (legacy), `~/.config/jarvis/config.toml`, defaults.
See [`config.example.toml`](../config.example.toml) for a commented file.

| config.toml | Environment variable | Default | Meaning |
|---|---|---|---|
| `general.backend` | `JARVIS_BACKEND` | none (asked by `jarvis setup`) | `local` or `gemini` |
| `general.language` | `JARVIS_LANGUAGE` | `auto` | `auto en fr ar ar-tn es de it pt nl ru tr zh` (`ar-tn` is Tunisian Arabic) |
| `general.persona` | `JARVIS_PERSONA` | `sidekick` | `sidekick friendly concise` |
| `general.wake_word` | `JARVIS_WAKE_WORD` | `false` | say "hey jarvis" first (needs `--extra wakeword`) |
| `general.briefing` | `JARVIS_BRIEFING` | `true` | briefing on the first conversation each day (4 a.m. rollover) |
| `general.day_tracking` | `JARVIS_DAY_TRACKING` | on under Hyprland, else off | focused-app time, window class only |
| `audio.input_device` / `output_device` | `JARVIS_INPUT_DEVICE` / `JARVIS_OUTPUT_DEVICE` | system default | index or name fragment (`jarvis --devices`) |
| `audio.barge_in` | `JARVIS_BARGE_IN` | `false` | interrupt Jarvis by talking; needs headphones or echo cancellation |
| `audio.wake_threshold` | `JARVIS_WAKE_THRESHOLD` | `0.5` | lower triggers more easily |
| `audio.idle_timeout` | `JARVIS_IDLE_TIMEOUT` | `20` | seconds of silence before it stops listening (wake-word mode) |
| `local.llm_url` | `JARVIS_LLM_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible server |
| `local.llm_model` | `JARVIS_LLM_MODEL` | `llama3.2:3b` | must support tool calling |
| `local.stt_model` | `JARVIS_STT_MODEL` | `small` | Whisper size |
| `local.stt_device` | `JARVIS_STT_DEVICE` | `auto` | `auto cpu cuda` |
| `local.tts_voice` | `JARVIS_TTS_VOICE` | per language | a Piper voice name |
| `local.allow_remote_llm` | `JARVIS_ALLOW_REMOTE_LLM` | `false` | allow a non-loopback `llm_url` |
| `gemini.voice` | `JARVIS_VOICE` | `Charon` | Gemini voice |
| `gemini.model` | `JARVIS_MODEL` | auto | pin a Live model |
| `gemini.google_search` | `JARVIS_GOOGLE_SEARCH` | `true` | Google Search for news and weather |
| `safety.guard` | `JARVIS_GUARD` | `true` | refuse unrecoverable commands |
| `safety.deny_paths` | `JARVIS_DENY_PATHS` | none | extra off-limits paths (list, or colon-separated) |
| `safety.shell_timeout` | `JARVIS_SHELL_TIMEOUT` | `60` | seconds |
| `safety.max_output` | `JARVIS_MAX_OUTPUT` | `4000` | characters of command output the model sees |
| (env only) | `GEMINI_API_KEY` | none | the Gemini key |
| (env only) | `JARVIS_MUSIC_PLAYER`, `JARVIS_MUSIC_BROWSER` | browser, `xdg-open` | `mpv` for headless music |

Files Jarvis keeps: `~/.config/jarvis/me.md` (a note about you, you edit it),
`~/.local/share/jarvis/memory.md` (what it remembers), `~/.local/state/jarvis/commands.log`
(audit log), `~/.local/state/jarvis/activity/` (screen-time summaries).
