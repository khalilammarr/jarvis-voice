# Troubleshooting

Start with `jarvis doctor`. It checks audio, packages, the LLM server, models and your key.

| Symptom | Cause and fix |
|---|---|
| "Jarvis isn't set up yet" | Run `jarvis setup`. |
| `Can't reach the language-model server` | Start Ollama (`ollama serve`), or fix `llm_url`. |
| `The server doesn't have the model` | `ollama pull <model>` (the name is in the message). |
| "doesn't support tool calling" | Pick a tool-capable model (`llama3.2:3b`, `qwen2.5`, `qwen3`). |
| "voice isn't downloaded yet" | `jarvis setup` downloads it; or set `tts_voice` to one you have. |
| Replies are slow (local) | Use a smaller `llm_model` or `stt_model`; check the GPU is used (`nvidia-smi`). |
| It answers things you didn't say | Whisper heard noise. Use a headset, or wake-word mode. |
| It interrupts itself | Its voice reaches the mic. Keep `barge_in = false`, use headphones, or set up echo cancellation. |
| `llm_url ... is not this machine` | The privacy guard. Use localhost, or `allow_remote_llm = true` if you mean it. |
| Gemini: `exceeded your current quota` | The model has no free-tier quota. Pin one with `jarvis --list-models` and `gemini.model`. |
| Gemini: HTTP 404 on connect | AI Studio keys need the `v1alpha` Live endpoint (already used). Check the model name. |
| No sound / wrong mic | `jarvis --devices`, then set `input_device` / `output_device`. |
| `Refused: ... off-limits` | The path filter working as intended; adjust `deny_paths` / `DEFAULT_DENY_PATHS`. |
