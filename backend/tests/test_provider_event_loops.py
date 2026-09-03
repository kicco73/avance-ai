"""The LLM providers are single app-wide instances driven from several
event loops at once: the main FastAPI loop, every JobQueue worker's own
long-lived loop (test replays run up to max-concurrent-tests of them in
parallel), and the one-shot loop each PromptContext._run_sync spins up.
An httpx-based SDK client shared across those loops reuses keep-alive
connections opened on another loop — sporadic connection errors under
load, reproduced here against a local fake API (with the SDK's own
silent retries off, which is now also the shipped configuration).
Every provider must therefore be safe to drive from any loop, and must
never wait forever on a silent upstream.
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import pytest

from ai.anthropic_provider_v2 import AnthropicProvider, SDK_MAX_RETRIES as ANTHROPIC_SDK_MAX_RETRIES
from ai.gemini_provider_v2 import GeminiProvider, REQUEST_TIMEOUT_MS
from ai.llm_provider import AIServiceConfig
from ai.openai_provider_v2 import OpenAICompatibleProvider

pytestmark = pytest.mark.contract

EXPECTED = '{"text": "hi"}'


class _FakeApi(BaseHTTPRequestHandler):
    """Streams one tiny JSON reply in the wire shape of whichever API the
    path names, over keep-alive HTTP/1.1 like the real services do, with
    a little jitter so that connections genuinely interleave."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        time.sleep(random.uniform(0.002, 0.02))
        if self.path.endswith("/messages"):
            events = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "m", "type": "message", "role": "assistant", "model": "c", "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 2, "output_tokens": 0},
                }}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": EXPECTED}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
        else:
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": EXPECTED[:8]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": EXPECTED[8:]}, "finish_reason": "stop"}],
                 "usage": {"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 1}},
            ]
            body = "".join(
                "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m", **chunk}) + "\n\n"
                for chunk in chunks
            ) + "data: [DONE]\n\n"
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def fake_api_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _anthropic(fake_api_url: str) -> AnthropicProvider:
    provider = AnthropicProvider(AIServiceConfig("anthropic", "c", "k", None, "x"))
    # Point every per-loop client at the fake API.
    provider._new_async_client = lambda: anthropic.AsyncAnthropic(  # type: ignore[method-assign]
        api_key="k", base_url=fake_api_url, timeout=10.0, max_retries=ANTHROPIC_SDK_MAX_RETRIES,
    )
    return provider


def _openai(fake_api_url: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(AIServiceConfig("openai", "m", "k", fake_api_url + "/v1", "x"))


async def _one_call(provider) -> str:
    out = ""
    async for chunk in provider.generate_stream_with_schema("s", [{"role": "user", "content": "q"}], {"text": "t"}):
        out += chunk
    return out


def _drive_from_worker_loops(provider, *, workers: int = 4, calls: int = 40) -> dict[int, str]:
    """`workers` threads, each with its own long-lived loop (the JobQueue
    shape), hammering the shared provider. Returns {worker: error}."""
    errors: dict[int, str] = {}

    def worker(index: int) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for _ in range(calls):
                result = loop.run_until_complete(asyncio.wait_for(_one_call(provider), timeout=15))
                assert result == EXPECTED, result
        except Exception as exc:  # noqa: BLE001 — the failure *is* the finding
            errors[index] = f"{type(exc).__name__}: {exc}"
        finally:
            loop.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a worker hung"
    return errors


@pytest.mark.parametrize("make_provider", [_anthropic, _openai], ids=["anthropic", "openai"])
def test_a_shared_provider_survives_concurrent_worker_loops(fake_api_url, make_provider):
    provider = make_provider(fake_api_url)
    for _ in range(3):
        assert _drive_from_worker_loops(provider) == {}


@pytest.mark.parametrize("make_provider", [_anthropic, _openai], ids=["anthropic", "openai"])
def test_a_shared_provider_survives_one_shot_loops_after_worker_loops(fake_api_url, make_provider):
    """PromptContext._run_sync's shape: a fresh asyncio.run() per call,
    right after other loops filled the pool with keep-alive connections."""
    provider = make_provider(fake_api_url)
    assert _drive_from_worker_loops(provider, workers=2, calls=10) == {}
    for _ in range(5):
        assert asyncio.run(asyncio.wait_for(_one_call(provider), timeout=15)) == EXPECTED


def test_anthropic_keeps_one_client_per_loop_and_prunes_closed_ones(fake_api_url):
    provider = _anthropic(fake_api_url)
    for _ in range(5):
        asyncio.run(_one_call(provider))
    # Every one of those loops is closed by now; the next new loop's
    # first use sweeps them, so the dict never grows without bound.
    asyncio.run(_one_call(provider))
    assert len(provider._async_clients) == 1


def test_sdk_retries_are_off_and_timeouts_explicit():
    """The cascade is the one retry policy; a silent upstream must time
    out rather than hang a turn or a worker forever."""
    openai_provider = OpenAICompatibleProvider(AIServiceConfig("openai", "m", "k", None, "x"))
    assert openai_provider._client.max_retries == 0
    assert openai_provider._client.timeout.read == 60.0

    anthropic_provider = AnthropicProvider(AIServiceConfig("anthropic", "c", "k", None, "x"))
    assert anthropic_provider._sync_client.max_retries == 0
    assert anthropic_provider._new_async_client().max_retries == 0

    gemini_provider = GeminiProvider(AIServiceConfig("gemini", "m", "k", None, "x"))

    async def client():
        return gemini_provider._GeminiProvider__client()

    assert asyncio.run(client())._api_client._http_options.timeout == REQUEST_TIMEOUT_MS
    assert REQUEST_TIMEOUT_MS >= 30_000
