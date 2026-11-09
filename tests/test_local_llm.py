"""OpenAI-compatible streaming client: text deltas, tool-call assembly, friendly errors."""

from types import SimpleNamespace as NS

import httpx2 as httpx
import openai
import pytest

from jarvis.backends.local.errors import LocalBackendError
from jarvis.backends.local.llm import LLMClient, ToolCall


def chunk(content=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])


def tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class FakeCompletions:
    def __init__(self, chunks=None, error=None):
        self.chunks, self.error, self.kwargs = chunks or [], error, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error

        async def gen():
            for c in self.chunks:
                yield c
        return gen()


def client_with(completions):
    fake = NS(chat=NS(completions=completions))
    return LLMClient("http://127.0.0.1:11434/v1", "m", client=fake)


async def collect(llm, messages=None, tools=None):
    return [event async for event in llm.stream(messages or [{"role": "user", "content": "hi"}], tools or [])]


async def test_text_deltas_are_yielded_in_order():
    llm = client_with(FakeCompletions([chunk("Hel"), chunk("lo"), chunk(None)]))
    assert await collect(llm) == ["Hel", "lo"]


async def test_request_is_streaming_and_carries_model_and_tools():
    completions = FakeCompletions([chunk("x")])
    await collect(client_with(completions), tools=[{"type": "function"}])
    assert completions.kwargs["stream"] is True and completions.kwargs["model"] == "m"
    assert completions.kwargs["tools"] == [{"type": "function"}]


async def test_no_tools_key_when_there_are_none():
    completions = FakeCompletions([chunk("x")])
    await collect(client_with(completions))
    assert "tools" not in completions.kwargs


async def test_tool_call_fragments_are_assembled():
    llm = client_with(FakeCompletions([
        chunk(tool_calls=[tc(0, id="c1", name="run_shell", args='{"comm')]),
        chunk(tool_calls=[tc(0, args='and": "ls"}')]),
        chunk(tool_calls=[tc(1, id="c2", name="end_conversation", args="")]),
    ]))
    events = await collect(llm)
    assert events == [[ToolCall("c1", "run_shell", {"command": "ls"}),
                       ToolCall("c2", "end_conversation", {})]]


async def test_invalid_tool_arguments_become_a_reported_error():
    llm = client_with(FakeCompletions([chunk(tool_calls=[tc(0, id="c", name="run_shell", args="{oops")])]))
    [[call]] = await collect(llm)
    assert call.name == "run_shell" and call.error and call.arguments == {}


async def test_connection_failure_explains_how_to_start_the_server():
    request = httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions")
    llm = client_with(FakeCompletions(error=openai.APIConnectionError(request=request)))
    with pytest.raises(LocalBackendError, match="ollama serve"):
        await collect(llm)


async def test_missing_model_explains_how_to_pull_it():
    response = httpx.Response(404, request=httpx.Request("POST", "http://x/v1"))
    err = openai.NotFoundError("model not found", response=response, body=None)
    with pytest.raises(LocalBackendError, match="ollama pull m"):
        await collect(client_with(FakeCompletions(error=err)))


async def test_model_without_tool_support_is_explained():
    response = httpx.Response(400, request=httpx.Request("POST", "http://x/v1"))
    err = openai.BadRequestError("registry.ollama.ai/library/x does not support tools", response=response, body=None)
    with pytest.raises(LocalBackendError, match="tool"):
        await collect(client_with(FakeCompletions(error=err)), tools=[{"type": "function"}])
