# Jarvis

A voice assistant for Linux. Talk to your computer: ask questions, open apps, play music, run
commands, keep notes, get a morning briefing. You choose where the thinking happens.

| | **Local** (recommended) | **Gemini API** (testing only) |
|---|---|---|
| Speech to text | Whisper, on your machine | Google |
| Language model | Any model in [Ollama](https://ollama.com) (or LM Studio, llama.cpp) | Gemini Live |
| Voice | Piper, on your machine | Gemini voices |
| What leaves your computer | **Nothing** | Your microphone audio, command output, memories, profile |
| Setup | Ollama + about 2 GB of models | A free API key |
| Use it for | Everyday use, private work | Trying it out, non-sensitive chats |

> **Status.** The Gemini backend is the one used day to day by the author. The local backend is
> fully implemented and covered by unit tests with fake models, but it has had little time on real
> hardware. Treat it as beta and please report what you find (`jarvis doctor` output helps).

## Quick start

```bash
git clone https://github.com/khalilammarr/jarvis-voice && cd jarvis-voice
uv sync --extra local          # or --extra gemini, or --all-extras
uv run jarvis setup            # guided: brain, language, voice, personality
uv run jarvis doctor           # checks everything and tells you how to fix what's missing
uv run jarvis                  # just talk
```

For the local brain you also need Ollama running with a tool-capable model:

```bash
ollama pull llama3.2:3b
```

You need Python 3.11 or 3.12, PortAudio (`libportaudio2` / `portaudio`), and a working
PipeWire or PulseAudio setup.

## The setup wizard

`jarvis setup` asks five things and writes `~/.config/jarvis/config.toml`. Run it again any time.

1. **Brain.** Local or Gemini, with the privacy trade-off spelled out.
2. **Language.** Auto, English, French, Arabic, Spanish, German, Italian, Portuguese, Dutch,
   Russian, Turkish or Chinese. It sets the language Jarvis speaks and what Whisper listens for.
3. **Personality.** `sidekick` (casual, a bit of banter), `friendly` (warm, patient) or
   `concise` (short answers, no small talk).
4. **Voice.** Local: the Piper voice for your language is downloaded for you. Gemini: pick a
   voice by name, or audition all of them with `jarvis --voices`.
5. **Behaviour.** Wake word or always listening, and the daily briefing.

The reference for every setting is in [docs/configuration.md](docs/configuration.md).

## What it can do

```
mic -> speech to text -> language model -> text to speech -> speaker
                              | tool calls
   run_shell · open_app · play_music · media_control · remember / recall / forget
```

Say things like "open the browser", "what's using the most memory", "play some lo-fi",
"remember that the dentist is on the 12th", "pause the music", "goodbye".

## Safety

Jarvis can run **any** shell command as you. That is the point, and it is also the risk.
Read [docs/privacy-and-safety.md](docs/privacy-and-safety.md) before you use it. In short:

* Every command is written to an audit log (output is never logged).
* A guard refuses unrecoverable commands (`rm -rf /`, `mkfs`, `dd` onto a disk, fork bombs).
* A path filter refuses commands that touch SSH keys, cloud credentials, browser profiles and
  `.env` files, and secret-looking strings are scrubbed from output. You can add your own paths.
* The prompt makes Jarvis announce destructive actions and wait for a spoken "yes".

None of this is a sandbox. Speech recognition mishears, and language models make mistakes.

## More

* [Backends: local vs Gemini](docs/backends.md)
* [Configuration reference](docs/configuration.md)
* [Privacy and safety](docs/privacy-and-safety.md)
* [Desktop integration: systemd, Hyprland, Omarchy bar widget](docs/desktop-integration.md)
* [Troubleshooting](docs/troubleshooting.md)
* [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [MIT license](LICENSE)
