# Desktop integration (optional)

The core works on any Linux with PipeWire or PulseAudio. These extras are in `contrib/`.

## Run as a user service

```bash
uv tool install '.[local]'          # puts `jarvis` in ~/.local/bin (or use pipx)
mkdir -p ~/.config/systemd/user && cp contrib/jarvis.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start jarvis        # enable it to start at login
journalctl --user -fu jarvis -o cat  # the transcript
```

`systemctl --user kill --kill-whom=main -s USR1 jarvis` means "talk now" (skips the wake word).
Jarvis writes `starting`, `listening`, `talking` or `stopped` to `$XDG_RUNTIME_DIR/jarvis/state`.

## Keyboard shortcut

`contrib/jarvis-toggle` starts or stops the service. Bind it in your compositor, e.g. Hyprland:

```
bind = SUPER CTRL, J, exec, /path/to/jarvis-voice/contrib/jarvis-toggle
```

## Omarchy / Quickshell bar widget

`contrib/omarchy/jarvis-voice/` is a bar widget: left click starts or stops the service, right
click talks now, middle click opens the transcript.

```bash
cp -r contrib/omarchy/jarvis-voice ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins
omarchy plugin enable jarvis-voice.jarvis
omarchy-restart-shell                # hot reload does not replace a mounted widget
```

## Echo cancellation (to interrupt Jarvis on laptop speakers)

Copy `contrib/echo-cancel.conf` to `~/.config/pipewire/pipewire.conf.d/`, restart PipeWire,
use the "Echo-Cancel Source" as your input, and set `barge_in = true`.
