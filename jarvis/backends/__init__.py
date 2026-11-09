"""Conversation backends. ``make_conversation`` picks one from ``cfg.backend``.

* ``gemini``: Google's Live API. Audio goes to Google. For testing and non-sensitive use.
* ``local``: Whisper -> an OpenAI-compatible LLM server on this machine -> Piper. Nothing leaves it.
"""

from __future__ import annotations


def make_conversation(cfg, client, model, mic, speaker, tools, transcript, context=None, opening=None,
                      resume=None):
    """``client`` and ``model`` are the Gemini client and model name, or (local) the LocalRuntime."""
    if cfg.backend == "local":
        from jarvis.backends.local.conversation import LocalConversation

        return LocalConversation(client, model, cfg, mic, speaker, tools, transcript,
                                 context=context, opening=opening)
    from jarvis.session import Conversation  # looked up at call time so tests can patch it

    return Conversation(client, model, cfg, mic, speaker, tools, transcript,
                        context=context, opening=opening, resume=resume)
