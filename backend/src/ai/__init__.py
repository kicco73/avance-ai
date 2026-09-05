"""The AI service and its public contract.

    AiService          — the one object the rest of the app talks to a model
                         through (build with AiService.for_live/for_test)
    AIServiceConfig    — one configured provider entry (config.py parses it)
    AIServiceError     — base of every error a call can surface
    MetadataCallback   — the (key, value) tap a caller passes to receive
                         signals/env/audio/text as they stream
    content_to_text    — flattens attachment blocks to plain text, the way
                         every provider does before sending them
    SystemPrompt       — a system prompt split into a cache-friendly
                         stable prefix and a volatile tail (see its own
                         docstring) — the type every generate_stream_
                         with_* system_prompt parameter accepts alongside
                         a plain str.
    ToolSpec           — one native-tool declaration a caller's own
                         ToolSet (tracking.sources.ToolSet) hands to
                         AiService — the only ai.* type built outside
                         this package, so it alone needs to be public.
    ToolAbortDecider   — structural protocol generate_stream_with_
                         metadata's own tool_abort parameter expects;
                         TrackingProcessor conforms to it directly (see
                         its own should_abort_tools).

The concrete providers (Gemini, Anthropic, OpenAI-compatible) and the
failover cascade live in ai/_providers/ and are private: AiService is the
only consumer. tests/test_ai_package_boundary.py keeps it that way."""
from .ai_service import AiService, ToolAbortDecider
from .llm_provider import AIServiceConfig, AIServiceError, MetadataCallback, SystemPrompt, ToolSpec, content_to_text

__all__ = [
	"AiService", "AIServiceConfig", "AIServiceError", "MetadataCallback", "SystemPrompt", "ToolSpec", "content_to_text",
	"ToolAbortDecider",
]
