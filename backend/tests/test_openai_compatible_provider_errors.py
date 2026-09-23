from __future__ import annotations

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from ai.llm_provider import AIServiceProviderPermanentError, AIServiceRequestError
from ai.response_schema import StringField
from provider_tools_helpers import OpenAIHarness

harness = OpenAIHarness()

REQUEST = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")


@pytest.mark.asyncio
async def test_connection_refused_cascades_instead_of_getting_stuck() -> None:
    """A local llama.cpp/LM Studio server that isn't running raises
    openai.APIConnectionError — never an HTTP status, since the request
    never reached a server at all. This must map to
    AIServiceProviderPermanentError (a cascade failover trigger, see
    cascading_llm_provider.py's _FAILOVER_ERRORS), not the generic
    AIServiceError, or the cascade pointer never advances and every
    subsequent call keeps retrying the same dead provider forever."""
    connection_error = APIConnectionError(message="Connection refused", request=REQUEST)
    provider, _ = harness.provider([], errors=[connection_error])

    with pytest.raises(AIServiceProviderPermanentError):
        async for _ in provider.generate_stream_with_schema("system prompt", [], {"text": StringField("t")}):
            pass


@pytest.mark.asyncio
async def test_bad_request_maps_to_request_error_not_permanent() -> None:
    response = httpx.Response(400, request=REQUEST, json={"message": "invalid_request_message_order"})
    status_error = APIStatusError(message="invalid_request_message_order", response=response, body=None)
    provider, _ = harness.provider([], errors=[status_error])

    with pytest.raises(AIServiceRequestError):
        async for _ in provider.generate_stream_with_schema("system prompt", [], {"text": StringField("t")}):
            pass
