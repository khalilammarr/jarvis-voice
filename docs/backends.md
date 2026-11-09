# Backends

Choose with `jarvis setup`, or `backend = "local" | "gemini"` in `config.toml`.

## Local (recommended)

```
mic -> energy VAD -> faster-whisper -> OpenAI-compatible LLM server -> Piper -> speaker
```

Everything runs on your machine. The only network connection Jarvis makes for thinking is to the
LLM server you configure, and by default Jarvis **refuses** any `llm_url` that is not
`localhost`, `127.0.0.1` or `::1`. To use a server on another machine on purpose, set
`allow_remote_llm = true`.

**Language model.** Anything that speaks the OpenAI chat API with tool calling: Ollama
(`http://127.0.0.1:11434/v1`), LM Studio, llama.cpp's `llama-server`, vLLM. The model must support
tools. `llama3.2:3b` is the small default; on 6 GB of VRAM a 7-8B model at Q4 also fits. Reasoning
models work (their `<think>` text is never spoken) but answer more slowly.

**Speech to text.** `stt_model` is a Whisper size (`tiny`, `base`, `small`, `medium`). On NVIDIA
GPUs it uses CUDA automatically; otherwise it runs int8 on the CPU. Models download on first use.

**Voice.** One Piper voice per language, downloaded by `jarvis setup` into
`~/.local/share/jarvis/voices/`. Override with `tts_voice`. Browse voices in the
[Piper voice list](https://huggingface.co/rhasspy/piper-voices).

**Tunisian Arabic.** There is no Whisper or Piper model for the Derja dialect. With `language = "ar-tn"` the local backend
listens for standard Arabic and speaks with an Arabic Piper voice, so expect it to sound off. Use Gemini for Derja.

**Limits.** No web search (nothing is allowed out), so the daily briefing has no weather. Speech
detection is energy-based, so a very noisy room may need a headset. No streaming speech-to-text:
Jarvis waits for you to finish a sentence (about 0.6 s of silence), then transcribes it.

## Gemini API (for testing, or if you lack the RAM for a local model)

Uses the Gemini Live API for speech in, thinking and speech out in one connection. Quick to start
and sounds natural, but **your microphone audio, and anything Jarvis reads for the model (command
output, your profile, memories, window-class screen time), is sent to Google**. Free-tier keys
may be used to improve Google's products. Use it to try Jarvis and for conversations you would be
fine saying out loud in public.

It needs a key from <https://aistudio.google.com/apikey>. `jarvis setup` stores it in
`~/.config/jarvis/secrets.env` with mode 0600, never in `config.toml`.

Jarvis keeps one session open in always-on mode and reconnects when Google rotates it. Model
availability on the free tier changes; see `jarvis --list-models` and [troubleshooting](troubleshooting.md).
