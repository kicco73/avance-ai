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
from provider_tools_helpers import AnthropicHarness, GeminiHarness, OpenAIHarness, drain
from turn_harness import PROJECT_ID, one_state_automaton, turn_service_for  # noqa: F401 — a pytest fixture, used by name

pytestmark = pytest.mark.contract

FRAGMENTS = [{"role": "user", "content": ["I have a problem", "with VY3003"]}]


def _session(db) -> int:
    db.ensure_project("proj")
    db.publish_project("proj")
    return db.create_chat_session(
        username="user", project_id="proj", revision=db.get_project_published_revision("proj"),
    )


def _contents(db, session_id: int) -> list:
    return [entry["content"] for entry in db.get_turn_history(session_id, None, None)]


class TestGrouping:
    """`answered_by` is the grouping key, not adjacency: it says which turn
    a user message belongs to, which stored ids alone no longer do once a
    fragment can arrive while the previous turn is still generating."""

    def test_the_fragments_of_one_turn_become_one_entry_of_several_texts(self, db):
        session_id = _session(db)
        fragments = [db.save_message("user", text, session_id) for text in ("hi", "I have a problem", "with flight VY3003")]
        reply = db.save_message("assistant", "Let me look.", session_id)
        db.mark_messages_answered(fragments, reply)

        assert _contents(db, session_id) == [
            ["hi", "I have a problem", "with flight VY3003"], "Let me look.",
        ]

    def test_the_group_carries_the_last_fragments_own_id(self, db):
        session_id = _session(db)
        opening = db.save_message("user", "a", session_id)
        closing = db.save_message("user", "b", session_id)
        reply = db.save_message("assistant", "r", session_id)
        db.mark_messages_answered([opening, closing], reply)

        assert db.get_turn_history(session_id, None, None)[0]["id"] == closing

    def test_a_lone_fragment_keeps_a_plain_string_exactly_as_before(self, db):
        session_id = _session(db)
        asked = db.save_message("user", "hi", session_id)
        reply = db.save_message("assistant", "hello", session_id)
        db.mark_messages_answered([asked], reply)
        db.save_message("user", "again", session_id)

        assert _contents(db, session_id) == ["hi", "hello", "again"]

    def test_fragments_of_different_turns_are_never_merged(self, db):
        session_id = _session(db)
        alone = db.save_message("user", "a", session_id)
        first_reply = db.save_message("assistant", "r", session_id)
        db.mark_messages_answered([alone], first_reply)
        opening = db.save_message("user", "b", session_id)
        closing = db.save_message("user", "c", session_id)
        second_reply = db.save_message("assistant", "r2", session_id)
        db.mark_messages_answered([opening, closing], second_reply)

        assert _contents(db, session_id) == ["a", "r", ["b", "c"], "r2"]

    def test_a_fragment_stored_before_the_previous_turns_reply_still_reads_after_it(self, db):
        """The interleaving this whole key exists for: B arrived while the
        turn answering A was still generating, so B's id precedes that
        reply's — but B belongs to the next turn, and must read that way."""
        session_id = _session(db)
        a = db.save_message("user", "A", session_id)
        b = db.save_message("user", "B", session_id)
        answer_to_a = db.save_message("assistant", "answer to A", session_id)
        answer_to_b = db.save_message("assistant", "answer to B", session_id)
        db.mark_messages_answered([a], answer_to_a)
        db.mark_messages_answered([b], answer_to_b)

        assert _contents(db, session_id) == ["A", "answer to A", "B", "answer to B"]


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

    async def test_anthropic_sends_one_user_message_with_a_text_block_per_fragment(self):
        harness = AnthropicHarness()
        provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])

        await drain(provider.generate_stream_with_schema("sys", FRAGMENTS, {"text": "t"}))

        assert harness.calls(fake_client)[0]["messages"] == [{
            "role": "user",
            "content": [
                {"type": "text", "text": "I have a problem"},
                {"type": "text", "text": "with VY3003"},
            ],
        }]

    async def test_openai_sends_one_user_message_with_a_text_part_per_fragment(self):
        harness = OpenAIHarness()
        provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])

        await drain(provider.generate_stream_with_schema("sys", FRAGMENTS, {"text": "t"}))

        assert harness.calls(fake_client)[0]["messages"][1:] == [{
            "role": "user",
            "content": [
                {"type": "text", "text": "I have a problem"},
                {"type": "text", "text": "with VY3003"},
            ],
        }]

    async def test_openai_still_sends_a_lone_message_as_a_plain_string(self):
        harness = OpenAIHarness()
        provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])

        await drain(provider.generate_stream_with_schema("sys", [{"role": "user", "content": "just one"}], {"text": "t"}))

        assert harness.calls(fake_client)[0]["messages"][1:] == [{"role": "user", "content": "just one"}]

    async def test_gemini_sends_one_content_with_a_part_per_fragment(self):
        harness = GeminiHarness()
        provider, fake_client = harness.provider([harness.text_response('{"text": "hi"}')])

        await drain(provider.generate_stream_with_schema("sys", FRAGMENTS, {"text": "t"}))

        contents = harness.calls(fake_client)[0]["contents"]
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
async def _listening(turn_service, db, session_id):
    """The listener, and a way to say something through it — accepting is
    immediate, answering is not (see turn/input_listener.py)."""
    from system import bus
    from system.bus import INPUT_TEXT, Message
    from system.web_session import WebSession
    from turn.input_listener import TurnInput

    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    bus._reset_for_tests()
    TurnInput(turn_service, db).register()

    async def say(text: str) -> None:
        await bus.publish(Message(
            type=INPUT_TEXT, body={"text": text}, username=WebSession().user,
            session_id=session_id, channel="webchat", origin_id="connection-1",
        ))

    return say


async def test_messages_arriving_while_a_turn_generates_are_answered_together_by_the_next_one(turn_service_for):
    """A is already being answered when B and C arrive: A is answered
    alone, and B and C are answered together as ONE user message of two
    blocks. Accepting is immediate — all three are in the transcript, in
    arrival order, before the first answer exists, and none is on disk
    until the answer that covers it is (see turn/turn_transaction.py) —
    and which of them an answer is for is decided when they are accepted
    (see turn/input_listener.py)."""
    provider = _GatedProvider()
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    session_id = session["id"]
    say = await _listening(turn_service, db, session_id)

    await say("A")
    await _wait_for(provider.first_round_started.is_set)
    await say("B")
    await say("C")
    await _wait_for(lambda: len([m for m in turn_service.read_history(session_id) if m["role"] == "user"]) == 3)
    assert [m["role"] for m in turn_service.read_history(session_id)] == ["user", "user", "user"]
    assert db.get_messages(session_id) == []

    provider.release.set()
    await _wait_for(lambda: len([m for m in db.get_messages(session_id) if m["role"] == "assistant"]) == 2)

    assert _last_user_content(provider.histories[0]) == "A"
    assert _last_user_content(provider.histories[1]) == ["B", "C"]
    assert len(provider.histories) == 2
    persisted = db.get_messages(session_id)
    assert [m["role"] for m in persisted] == ["user", "assistant", "user", "user", "assistant"]
    assert [m["content"] for m in persisted if m["role"] == "user"] == ["A", "B", "C"]


@pytest.mark.regression
async def test_the_coalesced_turn_binds_to_its_last_fragment(turn_service_for):
    """The last fragment is the one that closes the turn, so it is where
    the turn's own user-side facts land (its Tracking row, the bot's
    reaction, the input tokens) — visible here as the turn's
    user_message_id."""
    provider = _GatedProvider()
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    session_id = session["id"]

    say = await _listening(turn_service, db, session_id)
    await say("A")
    await _wait_for(provider.first_round_started.is_set)
    await say("B")
    await say("C")
    await _wait_for(lambda: len([m for m in turn_service.read_history(session_id) if m["role"] == "user"]) == 3)
    provider.release.set()
    await _wait_for(lambda: len([m for m in db.get_messages(session_id) if m["role"] == "assistant"]) == 2)
    reloaded = [entry["content"] for entry in db.get_turn_history(session_id, None, None)]
    assert reloaded == ["A", "answer 1", ["B", "C"], "answer 2"]


@pytest.mark.regression
async def test_the_history_reloaded_afterwards_is_the_one_the_model_was_sent(turn_service_for):
    provider = _GatedProvider()
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    session_id = session["id"]

    say = await _listening(turn_service, db, session_id)
    await say("A")
    await _wait_for(provider.first_round_started.is_set)
    await say("B")
    await say("C")
    await _wait_for(lambda: len([m for m in turn_service.read_history(session_id) if m["role"] == "user"]) == 3)
    provider.release.set()
    await _wait_for(lambda: len([m for m in db.get_messages(session_id) if m["role"] == "assistant"]) == 2)

    reloaded = [entry["content"] for entry in db.get_turn_history(session_id, None, None)]
    assert reloaded == ["A", "answer 1", ["B", "C"], "answer 2"]


@pytest.mark.regression
async def test_the_history_budget_drops_a_half_cut_group_whole(turn_service_for):
    """A budget that can only fit part of a turn's own fragments drops
    that turn entirely, rather than showing the model an opening message
    it never sees the rest of."""
    provider = _GatedProvider()
    provider.release.set()
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    session_id = session["id"]

    first = db.save_message("user", "one", session_id, tokens=10)
    second = db.save_message("user", "two", session_id, tokens=10)
    reply = db.save_message("assistant", "answered", session_id, tokens=10)
    db.mark_messages_answered([first, second], reply)
    later = db.save_message("user", "later", session_id, tokens=10)

    history = db.get_turn_history(session_id, None, 30)

    assert [entry["content"] for entry in history] == ["answered", "later"]


@pytest.mark.regression
async def test_the_history_budget_keeps_a_group_it_fits_entirely(turn_service_for):
    provider = _GatedProvider()
    provider.release.set()
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider)
    db = turn_service_for.db
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    session_id = session["id"]

    first = db.save_message("user", "one", session_id, tokens=10)
    second = db.save_message("user", "two", session_id, tokens=10)
    reply = db.save_message("assistant", "answered", session_id, tokens=10)
    db.mark_messages_answered([first, second], reply)

    history = db.get_turn_history(session_id, None, 100)

    assert [entry["content"] for entry in history] == [["one", "two"], "answered"]
