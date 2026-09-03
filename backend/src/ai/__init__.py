"""The AI service and its public contract.

    AiService          — the one object the rest of the app talks to a model
                         through (build with AiService.for_live/for_test)
    AIServiceConfig    — one configured provider entry (config.py parses it)
    AIServiceError     — base of every error a call can surface
    MetadataCallback   — the (key, value) tap a caller passes to receive
                         signals/env/audio/text as they stream
    content_to_text    — flattens attachment blocks to plain text, the way
                         every provider does before sending them

The concrete providers (Gemini, Anthropic, OpenAI-compatible) and the
failover cascade live in ai/_providers/ and are private: AiService is the
only consumer. tests/test_ai_package_boundary.py keeps it that way."""
from .ai_service import AiService
from .llm_provider import AIServiceConfig, AIServiceError, MetadataCallback, content_to_text

__all__ = ["AiService", "AIServiceConfig", "AIServiceError", "MetadataCallback", "content_to_text"]
