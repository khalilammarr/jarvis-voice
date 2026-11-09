# Contributing

Thanks for helping. Short version:

```bash
uv sync --all-extras
uv run pytest -q -p no:warnings --cov=jarvis      # must stay above 80%
```

* **Tests first.** New behaviour needs a failing test before the code. The speech, LLM and audio
  layers are all injectable, so tests need no models, network or sound card.
* **Layout.** `jarvis/backends/` holds one folder per brain (`gemini` lives in `session.py` for now,
  `local/`). The shared parts (tools, shell guard, memory, prompt) are backend-neutral. Keep it that way.
* **Desktop integrations** (Hyprland, Quickshell, systemd) belong in `contrib/` and must stay optional.
* **Privacy.** The local backend must never send audio or text anywhere but the configured
  loopback LLM server. Anything that reads local data for the model goes through `jarvis/redact.py`.
* **Languages.** To add one, add a row to `LANGUAGES` in `jarvis/languages.py` with a Piper voice
  from the [voice list](https://huggingface.co/rhasspy/piper-voices) and a test run of `jarvis setup`.
* Commit style: `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`.
