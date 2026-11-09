<p align="center">
  <img src="docs/banner.svg" alt="Jarvis: talk to your Linux desktop" width="100%">
</p>

When I was a kid I watched Tony Stark talk to his house and his suit, and it just worked. I wanted that
setup. Not the suit, the part where you say what you want and the computer does it.

It's now possible to build that on a normal PC, so I did.

I know assistants like this already exist, and I'm not going to pretend this one is the answer to
anything. How useful it really is can be debated. I built it because it's fun to have on my own machine,
and because there are two things I end up using it for all the time:

- **Typing.** I type a lot, and sometimes my hands start to hurt. So I tell Jarvis what to write and it
  types it into whatever window I'm in.
- **Being lazy.** When I can't be bothered to search for something, I ask. "Open the browser", "what's
  eating my RAM", "play some lo-fi".

That's the project. It's a hobby tool that I run at home, and I'm putting it up in case someone else
wants the same thing.

## Not finished

This is a work in progress and I change it often. Commands, config keys and file locations can move
between commits, things will break, and a few parts are only half built. There are no releases yet. If
you want something stable, pin a commit; if you pull the latest, skim the commit log first.

## How it works

You talk, it listens, something thinks, it talks back and presses the buttons.

```
mic -> speech to text -> a language model -> text to speech -> speaker
                              |
              tools: run commands, open apps, play music,
                     type for you, remember things
```

The "something that thinks" is your choice. There are two options and you pick one during setup.

**Gemini** is what I run. It's Google's Live API: you paste a free key and it works in a couple of
minutes, and it sounds good. The catch is that your microphone audio, and anything Jarvis reads out
for the model (command output, the notes about you, what you asked it to remember), goes to Google,
and free keys may be used to train their models. I use it for things I'd be fine saying out loud
in a café.

**Local** keeps everything on your machine: Whisper for listening, a model running in
[Ollama](https://ollama.com) for thinking, Piper for the voice. Nothing leaves. If you're going to use
this for real work, use this one. I'll be straight about it: I wrote it and it has unit tests, but I
haven't run it on my own computer, because I use Gemini. If you try it and something breaks, please open
an issue with the output of `jarvis doctor`.

## Install

You need Python 3.11 or 3.12, PortAudio (`portaudio` on Arch, `libportaudio2` on Debian and Ubuntu),
and PipeWire or PulseAudio for sound.

```bash
git clone https://github.com/khalilammarr/jarvis-voice
cd jarvis-voice
uv sync --extra gemini        # or --extra local, or --all-extras
uv run jarvis setup           # a few questions, see below
uv run jarvis doctor          # checks your install and tells you how to fix what's missing
uv run jarvis                 # then just talk
```

For the local brain, also run an Ollama server with a model that can call tools:

```bash
ollama pull llama3.2:3b
```

### Setup

`jarvis setup` asks which brain to use, which language Jarvis should speak (auto, English, French,
Arabic, Spanish, German, Italian, Portuguese, Dutch, Russian, Turkish or Chinese), what personality it
should have (casual with a bit of banter, friendly, or short and to the point), which voice, and whether
to wait for a wake word or just listen. It writes `~/.config/jarvis/config.toml`. Run it again whenever
you change your mind. If you use Gemini, `jarvis --voices` plays every voice so you can pick one.

## Typing for you

This one is new and still rough. Say something like:

- "type: I'll be there at six, don't wait for me"
- "write a reply saying thanks, I'll look at it tonight"
- "type git status and press enter"

Jarvis types the text into whatever window has focus, as if you'd typed it. It only presses Enter when
you ask it to. It needs a tool that can send keystrokes:

```bash
sudo pacman -S wtype          # Hyprland, Sway and other wlroots compositors
sudo pacman -S xdotool        # X11
sudo pacman -S ydotool        # anything else (GNOME on Wayland, for example)
```

The audit log records how many characters were typed, never the text, since you might be typing a
password.

## What else it does

- Runs shell commands, launches apps, plays music from YouTube, controls whatever is playing.
- Remembers things you tell it, in a plain text file you can edit (`~/.local/share/jarvis/memory.md`).
- Reads a short note about you (`~/.config/jarvis/me.md`) so it knows your city, your habits and what you do.
- Gives a quick briefing the first time you talk to it each day.
- On Hyprland it can tell how long you've spent in each app. Only the app name is stored, never window titles.
- Says goodbye when you do, and turns itself off.

## Where it's going

Ticked means it exists today. The empty boxes are things I want to build, roughly in the order I'd
get to them. The list will change.

- [x] Talk to your desktop and hear it answer (Gemini Live)
- [x] Run commands, open apps, play music, control what's playing
- [x] Type for you, into whatever window has focus
- [x] Memory: it remembers what you tell it, in a plain text file you can edit
- [x] Daily briefing, three personalities, 12 languages, a setup wizard, `jarvis doctor`
- [ ] A fully local brain. The code is written and tested with fake models, but it hasn't run on real
  hardware yet, so I'm not ticking it.

Not built yet:

- [ ] **Skills.** Small add-on abilities kept as plain files, like "set up my work desktop" or
  "summarise what's on my clipboard", that Jarvis can pick up without me touching the core.
- [ ] **Screen reading.** Grab a screenshot, run character recognition on it, and let you say "what does
  this error say?" or "read me that email". Useful when the text can't be copied.
- [ ] **Better memory.** Searchable, grouped by topic, able to answer "what was I doing yesterday?",
  and easy to prune. Today it's a flat list of facts.
- [ ] **Agents.** Helpers that keep working after you stop talking: "tell me when this download
  finishes", "watch the build and speak up if it fails", a research mode that goes off, reads a few
  pages and reports back.
- [ ] **A real dictation mode.** Say "dictation on" and everything you say is typed word for word,
  with no model in the loop, until you say "dictation off". Faster, and nothing leaves the machine.
- [ ] **Local speech detection** so silence is never streamed anywhere, plus a mute hotkey.
- [ ] Setup guides for GNOME and KDE, not only Hyprland.

## Inspiration

[OpenJarvis](https://github.com/open-jarvis/OpenJarvis), from Stanford's Hazy Research and Scaling
Intelligence Lab, is a much bigger and more serious take on local-first personal AI, with skills,
agents and ready-made presets. Seeing how they separate skills from agents is what got me thinking
about the list above. I didn't copy any code or text from it. If you want a research-grade framework
rather than a hobby project that talks to one person's desktop, go there.

## Be careful with it

Jarvis can run any command as you. That's what makes it useful and it's also the risk, so read
[docs/privacy-and-safety.md](docs/privacy-and-safety.md) before you leave it running. There's an audit
log, a guard that refuses things like `rm -rf /`, a filter that keeps it away from your SSH keys, cloud
credentials and browser profiles, and the prompt makes it say what it's about to do before anything
destructive. None of that is a sandbox. Speech recognition mishears and models get things wrong.

## More

[Backends](docs/backends.md) · [Configuration](docs/configuration.md) ·
[Privacy and safety](docs/privacy-and-safety.md) · [Desktop integration](docs/desktop-integration.md)
(systemd service, keyboard shortcut, a bar widget for Omarchy) · [Troubleshooting](docs/troubleshooting.md) ·
[Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
