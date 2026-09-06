"""A turn consumes every user message that arrived since the last reply
and answers them all at once, as ONE user message of several text blocks —
never a concatenated string. Signals and triggers are evaluated once per
turn, and everything that binds to "the user's message" binds to the last
fragment, the one that closes the turn.
"""
from __future__ import annotations

import asyncio

import pytest

from ai.llm_provider import content_to_text, is_text_fragments
from db.messages import _group_user_fragments
from test_ws_turn_event_order import _automaton, chat_service_for  # noqa: F401 — a pytest fixture, used by name

pytestmark = pytest.mark.contract


class TestGrouping:
    """`answered_by` is the grouping key, not adjacency: it says which turn
    a user message belongs to, which stored ids alone no longer do once a
    fragment can arrive while the previous turn is still generating."""

    def test_the_fragments_of_one_turn_become_one_entry_of_several_texts(self):
        rows = [
            {"id": 1, "role": "user", "content": "hi", "answered_by": 4},
            {"id": 2, "role": "user", "content": "I have a problem", "answered_by": 4},
            {"id": 3, "role": "user", "content": "with flight VY3003", "answered_by": 4},
            {"id": 4, "role": "assistant", "content": "Let me look.", "answered_by": None},
        ]

        grouped = _group_user_fragments(rows)

        assert [entry["content"] for entry in grouped] == [
            ["hi", "I have a problem", "with flight VY3003"], "Let me look.",
        ]

    def test_the_group_carries_the_last_fragments_own_id(self):
        rows = [
            {"id": 7, "role": "user", "content": "a", "answered_by": 10},
            {"id": 9, "role": "user", "content": "b", "answered_by": 10},
        ]

        assert _group_user_fragments(rows)[0]["id"] == 9

    def test_a_lone_fragment_keeps_a_plain_string_exactly_as_before(self):
        rows = [
            {"id": 1, "role": "user", "content": "hi", "answered_by": 2},
            {"id": 2, "role": "assistant", "content": "hello", "answered_by": None},
            {"id": 3, "role": "user", "content": "again", "answered_by": None},
        ]

        assert [entry["content"] for entry in _group_user_fragments(rows)] == ["hi", "hello", "again"]

    def test_fragments_of_different_turns_are_never_merged(self):
        rows = [
            {"id": 1, "role": "user", "content": "a", "answered_by": 2},
            {"id": 2, "role": "assistant", "content": "r", "answered_by": None},
            {"id": 3, "role": "user", "content": "b", "answered_by": 5},
            {"id": 4, "role": "user", "content": "c", "answered_by": 5},
        ]

        assert [entry["content"] for entry in _group_user_fragments(rows)] == ["a", "r", ["b", "c"]]

    def test_a_fragment_stored_before_the_previous_turns_reply_still_reads_after_it(self):
        """The interleaving this whole key exists for: B arrived while the
        turn answering A was still generating, so B's id precedes that
        reply's — but B belongs to the next turn, and must read that way."""
        rows = [
            {"id": 1, "role": "user", "content": "A", "answered_by": 3},
            {"id": 2, "role": "user", "content": "B", "answered_by": 4},
            {"id": 3, "role": "assistant", "content": "answer to A", "answered_by": None},
            {"id": 4, "role": "assistant", "content": "answer to B", "answered_by": None},
        ]

        assert [entry["content"] for entry in _group_user_fragments(rows)] == [
            "A", "answer to A", "B", "answer to B",
        ]


class TestContentToText:
    def test_fragments_join_with_a_newline_for_token_estimation_only(self):
        assert content_to_text(["a", "b"]) == "a\nb"

    def test_a_plain_string_is_untouched(self):
        assert content_to_text("a") == "a"

    def test_attachment_blocks_are_not_mistaken_for_fragments(self):
        blocks = [{"filename": "f.txt", "source": {"type": "text", "data": "x"}}]

        assert is_text_fragments(blocks) is False
        assert content_to_text(blocks) == "[Attachment: f.txt]\nx"


class TestProviderPayloads:
    """Every provider must render the fragments as several text blocks of
    ONE user message — never as separate turns, never concatenated."""

    def test_anthropic_sends_one_user_message_with_a_text_block_per_fragment(self):
        from ai._providers.anthropic_provider_v2 import AnthropicProvider
        from config import AIServiceConfig

        provider = AnthropicProvider(AIServiceConfig("anthropic", "claude-x", "k", None, "x"))

        messages = provider._build_messages([{"role": "user", "content": ["I have a problem", "with VY3003"]}])

        assert messages == [{
            "role": "user",
            "content": [
                {"type": "text", "text": "I have a problem"},
                {"type": "text", "text": "with VY3003"},
            ],
        }]

    def test_openai_sends_one_user_message_with_a_text_part_per_fragment(self):
        from ai._providers.openai_provider_v2 import OpenAICompatibleProvider
        from config import AIServiceConfig

        provider = OpenAICompatibleProvider(AIServiceConfig("openai", "gpt-x", "k", None, "x"))

        messages = provider._build_messages([{"role": "user", "content": ["I have a problem", "with VY3003"]}])

        assert messages == [{
            "role": "user",
            "content": [
                {"type": "text", "text": "I have a problem"},
                {"type": "text", "text": "with VY3003"},
            ],
        }]

    def test_openai_still_sends_a_lone_message_as_a_plain_string(self):
        from ai._providers.openai_provider_v2 import OpenAICompatibleProvider
        from config import AIServiceConfig

        provider = OpenAICompatibleProvider(AIServiceConfig("openai", "gpt-x", "k", None, "x"))

        messages = provider._build_messages([{"role": "user", "content": "just one"}])

        assert messages == [{"role": "user", "content": "just one"}]

    def test_gemini_sends_one_content_with_a_part_per_fragment(self):
        from ai._providers.gemini_provider_v2 import GeminiProvider
        from config import AIServiceConfig

        provider = GeminiProvider(AIServiceConfig("gemini", "gemini-x", "k", None, "x"))

        contents = provider._GeminiProvider__build_contents(
            [{"role": "user", "content": ["I have a problem", "with VY3003"]}]
        )

        assert len(contents) == 1
        assert contents[0].role == "user"
        assert [part.text for part in contents[0].parts] == ["I have a problem", "with VY3003"]


class _GatedProvider:
    """Holds the first round until released, and records the history it
    was handed on every round — what the model actually saw."""

    def __init__(self) -> None:
        self.first_round_started = asyncio.Event()
        self.release = asyncio.Event()
        self.histories: list[list[dict]] = []

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.histories.append([dict(m) for m in history])
        if len(self.histories) == 1:
            self.first_round_started.set()
            await self.release.wait()
        yield '{"text": "answer %d"}' % len(self.histories)

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.005)


def _last_user_content(history: list[dict]):
    return [m["content"] for m in history if m["role"] == "user"][-1]


@pytest.mark.regression
async def test_messages_arriving_while_a_turn_generates_are_answered_together_by_the_next_one(chat_service_for):
    """A is already generating when B and C arrive: A answers A alone, the
    next turn takes B and C together as one multi-block user message, and
    the third request ends with no reply of its own."""
    provider = _GatedProvider()
    chat_service = chat_service_for(_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    session_id = session["id"]

    first = asyncio.create_task(chat_service.process_turn(session_id, "A"))
    await _wait_for(provider.first_round_started.is_set)
    second = asyncio.create_task(chat_service.process_turn(session_id, "B"))
    third = asyncio.create_task(chat_service.process_turn(session_id, "C"))
    await _wait_for(lambda: len([m for m in db.get_messages(session_id) if m["role"] == "user"]) == 3)
    provider.release.set()
    answered_a, answered_b, answered_c = await asyncio.gather(first, second, third)

    # A was alone when its turn opened, so it is a plain string as always.
    assert _last_user_content(provider.histories[0]) == "A"
    # B and C reach the model as ONE user message of two blocks.
    assert _last_user_content(provider.histories[1]) == ["B", "C"]
    assert len(provider.histories) == 2

    assert answered_a["assistant_message_id"] is not None
    assert answered_b["assistant_message_id"] is not None
    # C's own request finds its message already consumed: no reply of its own.
    assert answered_c["assistant_message_id"] is None
    assert answered_c["reply"] == []

    # Stored in arrival order, which interleaves: B and C were written
    # while the reply to A was still being generated. Which turn each one
    # belongs to is recorded separately (see Message.answered_by), and it
    # is that, not the stored order, that the model is shown.
    persisted = db.get_messages(session_id)
    assert [m["role"] for m in persisted] == ["user", "user", "user", "assistant", "assistant"]
    assert [m["content"] for m in persisted if m["role"] == "user"] == ["A", "B", "C"]


@pytest.mark.regression
async def test_the_coalesced_turn_binds_to_its_last_fragment(chat_service_for):
    """The last fragment is the one that closes the turn, so it is where
    the turn's own user-side facts land (its Tracking row, the bot's
    reaction, the input tokens) — visible here as the turn's
    user_message_id."""
    provider = _GatedProvider()
    chat_service = chat_service_for(_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    session_id = session["id"]

    first = asyncio.create_task(chat_service.process_turn(session_id, "A"))
    await _wait_for(provider.first_round_started.is_set)
    second = asyncio.create_task(chat_service.process_turn(session_id, "B"))
    third = asyncio.create_task(chat_service.process_turn(session_id, "C"))
    await _wait_for(lambda: len([m for m in db.get_messages(session_id) if m["role"] == "user"]) == 3)
    provider.release.set()
    _, answered_b, _ = await asyncio.gather(first, second, third)

    last_fragment = [m for m in db.get_messages(session_id) if m["content"] == "C"][0]
    assert answered_b["user_message_id"] == last_fragment["id"]


@pytest.mark.regression
async def test_the_history_reloaded_afterwards_is_the_one_the_model_was_sent(chat_service_for):
    provider = _GatedProvider()
    chat_service = chat_service_for(_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    session_id = session["id"]

    first = asyncio.create_task(chat_service.process_turn(session_id, "A"))
    await _wait_for(provider.first_round_started.is_set)
    second = asyncio.create_task(chat_service.process_turn(session_id, "B"))
    third = asyncio.create_task(chat_service.process_turn(session_id, "C"))
    await _wait_for(lambda: len([m for m in db.get_messages(session_id) if m["role"] == "user"]) == 3)
    provider.release.set()
    await asyncio.gather(first, second, third)

    reloaded = [entry["content"] for entry in db.get_turn_history(session_id, None, None)]
    assert reloaded == ["A", "answer 1", ["B", "C"], "answer 2"]


@pytest.mark.regression
async def test_the_history_budget_drops_a_half_cut_group_whole(chat_service_for):
    """A budget that can only fit part of a turn's own fragments drops
    that turn entirely, rather than showing the model an opening message
    it never sees the rest of."""
    provider = _GatedProvider()
    provider.release.set()
    chat_service = chat_service_for(_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    session_id = session["id"]

    first = db.save_message("user", "one", session_id, tokens=10)
    second = db.save_message("user", "two", session_id, tokens=10)
    reply = db.save_message("assistant", "answered", session_id, tokens=10)
    db.mark_messages_answered([first, second], reply)
    later = db.save_message("user", "later", session_id, tokens=10)

    # Room for the reply, "later", and only ONE of the two fragments.
    history = db.get_turn_history(session_id, None, 30)

    assert [entry["content"] for entry in history] == ["answered", "later"]


@pytest.mark.regression
async def test_the_history_budget_keeps_a_group_it_fits_entirely(chat_service_for):
    provider = _GatedProvider()
    provider.release.set()
    chat_service = chat_service_for(_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = chat_service_for.db
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    session_id = session["id"]

    first = db.save_message("user", "one", session_id, tokens=10)
    second = db.save_message("user", "two", session_id, tokens=10)
    reply = db.save_message("assistant", "answered", session_id, tokens=10)
    db.mark_messages_answered([first, second], reply)

    history = db.get_turn_history(session_id, None, 100)

    assert [entry["content"] for entry in history] == [["one", "two"], "answered"]
