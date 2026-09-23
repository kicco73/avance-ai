from dataclasses import dataclass
from typing import Callable

from ai.turn.env_prompt_block import EnvPromptBlock
from ai.turn.prompt import (
    MemoryBatchPrompt, MemoryPrompt, OutputBatchPrompt, OutputPrompt, Prompt, SignalsBatchPrompt, SignalsPrompt,
    TextPrompt, build_output_definition_for_names, build_output_fields,
)
from ai.turn.turn_protocol_using_schema import TurnProtocolUsingSchema


@dataclass(frozen=True)
class AiTurnKit:
    Prompt: type
    TextPrompt: type
    MemoryPrompt: type
    MemoryBatchPrompt: type
    OutputPrompt: type
    OutputBatchPrompt: type
    SignalsPrompt: type
    SignalsBatchPrompt: type
    EnvPromptBlock: type
    TurnProtocolUsingSchema: type
    build_output_definition_for_names: Callable
    build_output_fields: Callable


DEFAULT = AiTurnKit(
    Prompt=Prompt, TextPrompt=TextPrompt, MemoryPrompt=MemoryPrompt, MemoryBatchPrompt=MemoryBatchPrompt,
    OutputPrompt=OutputPrompt, OutputBatchPrompt=OutputBatchPrompt, SignalsPrompt=SignalsPrompt,
    SignalsBatchPrompt=SignalsBatchPrompt, EnvPromptBlock=EnvPromptBlock,
    TurnProtocolUsingSchema=TurnProtocolUsingSchema, build_output_definition_for_names=build_output_definition_for_names,
    build_output_fields=build_output_fields,
)
