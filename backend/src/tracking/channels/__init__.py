"""Metadata channels — the schema/prompt-building unit for one field of a
turn's structured JSON response (see tracking.turn_protocol_using_schema).
Each channel owns everything specific to its own field, in its own module:
the static preamble and this turn's own dynamic content that make up its
slice of the system prompt, its one-line JSON-schema description, and how
to decode the raw string the model returns for it. Adding a new field to
what a turn can ask the model for means adding one channel module here and
appending an instance of it to whatever list a caller builds — nothing
else needs to change shape."""
from .audio import AudioChannel, EMBED_AUDIO_TAG_PROMPT
from .base import MetadataChannel
from .batch import BATCH_END_MARKER, BatchChannel, MetadataTurnMismatch
from .memory import EMBED_MEMORY_TAG_PROMPT, MemoryChannel
from .memory_batch import EMBED_MEMORY_BATCH_TAG_PROMPT, MemoryBatchChannel
from .reaction import EMBED_REACTION_TAG_PROMPT, ReactionChannel
from .signals import EMBED_SIGNAL_TAG_PROMPT, SignalsChannel
from .signals_batch import EMBED_SIGNAL_BATCH_TAG_PROMPT, SignalsBatchChannel
from .text import TextChannel
from .translate import EMBED_TRANSLATE_TAG_PROMPT, TranslateChannel

__all__ = [
	"AudioChannel", "BatchChannel", "MemoryBatchChannel", "MemoryChannel", "MetadataChannel", "MetadataTurnMismatch",
	"ReactionChannel", "SignalsBatchChannel", "SignalsChannel", "TextChannel", "TranslateChannel",
	"BATCH_END_MARKER", "EMBED_AUDIO_TAG_PROMPT", "EMBED_MEMORY_BATCH_TAG_PROMPT", "EMBED_MEMORY_TAG_PROMPT",
	"EMBED_REACTION_TAG_PROMPT", "EMBED_SIGNAL_BATCH_TAG_PROMPT", "EMBED_SIGNAL_TAG_PROMPT", "EMBED_TRANSLATE_TAG_PROMPT",
]
