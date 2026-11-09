"""Errors of the local backend. Messages are written for the person at the keyboard."""


class LocalBackendError(RuntimeError):
    """Something in the local pipeline (LLM server, speech models) needs the user's attention."""
