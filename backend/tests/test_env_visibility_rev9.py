"""Verify env visibility with revision 9 (actual deployed version)."""
import pytest

from project.archive.automaton_loader import AutomatonLoader
from tracking.env import Env, PersistedEnv
from tracking.env_prompt_block import EnvPromptBlock
from tracking.prompt import TextPrompt, MemoryPrompt, Prompt
from tracking.fixed_project_context import FixedProjectContext

PROJECT_ID = "vueling_refunds"


@pytest.fixture
def automaton_rev9(db):
    """Load the actual revision 9 from the database."""
    revision = 9
    return AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)


def test_customer_record_and_user_email_visibility_in_rev9(db, automaton_rev9):
    """Verify that customer_record and user_email are visible in rev 9."""

    # Check exported env keys
    exported = automaton_rev9.exported_env_keys()
    exported_names = {key.name for key in exported}

    print(f"\nExported env keys in rev 9: {exported_names}")
    for key in exported:
        print(f"  - {key.name}: ai_access={key.ai_access}")

    # Both should be in the exported list
    assert "customer_record" in exported_names, "customer_record should be exported in rev 9"
    assert "user_email" in exported_names, "user_email should be exported in rev 9"

    # Build env with some values
    env = PersistedEnv(db, FixedProjectContext(PROJECT_ID), 0)  # 0 = not a real session
    env.update_action_set({
        "customer_record": "codice_volo,datetime_volo\nVY1234,2026-09-15 10:00",
        "user_email": "john@example.com",
    })

    # Get the locate_booking state which reads env
    state = automaton_rev9.states["locate_booking"]
    env_block = EnvPromptBlock.for_state(env, automaton_rev9, state)

    if env_block:
        env_text = env_block.text()
        print(f"\nEnv block:\n{env_text}\n")

        # Both should appear
        assert "customer_record:" in env_text, "customer_record should appear in env_block"
        assert "user_email:" in env_text, "user_email should appear in env_block"

        # Verify they're not truncated
        assert "VY1234" in env_text, "customer_record value should be visible"
        assert "john@example.com" in env_text, "user_email value should be visible"
    else:
        raise AssertionError("env_block should not be None")
