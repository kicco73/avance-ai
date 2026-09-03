from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from ai.llm_provider import AIServiceConfig, AIServiceProviderPermanentError, AIServiceRequestError
from ai._providers.openai_provider_v2 import OpenAICompatibleProvider


def _provider() -> OpenAICompatibleProvider:
    config = AIServiceConfig(
        driver="openai", model="local-model", key=None, url="http://localhost:8080/v1",
        ui_label="local",
    )
    return OpenAICompatibleProvider(config)


@pytest.mark.asyncio
async def test_connection_refused_cascades_instead_of_getting_stuck() -> None:
    """A local llama.cpp/LM Studio server that isn't running raises
    openai.APIConnectionError — never an HTTP status, since the request
    never reached a server at all. This must map to
    AIServiceProviderPermanentError (a cascade failover trigger, see
    cascading_llm_provider.py's _FAILOVER_ERRORS), not the generic
    AIServiceError, or the cascade pointer never advances and every
    subsequent call keeps retrying the same dead provider forever."""
    provider = _provider()
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    connection_error = APIConnectionError(message="Connection refused", request=request)

    with patch.object(provider._client.chat.completions, "create", AsyncMock(side_effect=connection_error)):
        with pytest.raises(AIServiceProviderPermanentError):
            async for _ in provider.generate_stream_with_schema("system prompt", []):
                pass


@pytest.mark.asyncio
async def test_bad_request_maps_to_request_error_not_permanent() -> None:
    provider = _provider()
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"message": "invalid_request_message_order"})
    status_error = APIStatusError(message="invalid_request_message_order", response=response, body=None)

    with patch.object(provider._client.chat.completions, "create", AsyncMock(side_effect=status_error)):
        with pytest.raises(AIServiceRequestError):
            async for _ in provider.generate_stream_with_schema("system prompt", []):
                pass
