"""Streaming chat client for any OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from jarvis.backends.local.errors import LocalBackendError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    error: str | None = None  # set when the model produced arguments that are not valid JSON


def _parse_arguments(raw: str) -> tuple[dict, str | None]:
    if not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments were not valid JSON ({exc.msg})"
    return (value, None) if isinstance(value, dict) else ({}, "arguments were not a JSON object")


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "local", client=None):
        self._url, self._model = base_url, model
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=120.0, max_retries=0)
        self._client = client

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[str | list[ToolCall]]:
        """Yields text deltas as they arrive, then one list of ToolCalls if the model made any."""
        import openai

        kwargs = dict(model=self._model, messages=messages, stream=True, temperature=0.6)
        if tools:
            kwargs["tools"] = tools
        try:
            response = await self._client.chat.completions.create(**kwargs)
            pending: dict[int, dict] = {}
            async for part in response:
                if not part.choices:
                    continue
                delta = part.choices[0].delta
                if delta.content:
                    yield delta.content
                for fragment in delta.tool_calls or []:
                    slot = pending.setdefault(fragment.index, {"id": "", "name": "", "args": ""})
                    slot["id"] = fragment.id or slot["id"]
                    if fragment.function:
                        slot["name"] = fragment.function.name or slot["name"]
                        slot["args"] += fragment.function.arguments or ""
        except openai.APIConnectionError as exc:
            raise LocalBackendError(
                f"Can't reach the language-model server at {self._url}. Is it running? "
                "With Ollama: `ollama serve` (or `systemctl start ollama`).") from exc
        except openai.NotFoundError as exc:
            raise LocalBackendError(
                f"The server doesn't have the model {self._model!r}. With Ollama: `ollama pull {self._model}`.") from exc
        except openai.BadRequestError as exc:
            if "tool" in str(exc).lower():
                raise LocalBackendError(
                    f"The model {self._model!r} doesn't support tool calling, which Jarvis needs. "
                    "Pick a tool-capable one (e.g. llama3.2:3b, qwen3, qwen2.5).") from exc
            raise LocalBackendError(f"The language-model server rejected the request: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LocalBackendError(f"The language-model server returned an error: {exc}") from exc

        calls = []
        for index in sorted(pending):
            slot = pending[index]
            arguments, error = _parse_arguments(slot["args"])
            calls.append(ToolCall(slot["id"] or f"call-{index}", slot["name"], arguments, error))
        if calls:
            yield calls
