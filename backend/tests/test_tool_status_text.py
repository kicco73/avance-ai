from __future__ import annotations

import pytest

from chat.tool_status_text import tool_status_text

pytestmark = pytest.mark.contract


def test_select_names_the_source_and_the_values_it_searches_for():
    payload = {"method": "select", "label": "Flight records", "source": "flights", "arguments": {"values": ["VY3003"]}}

    assert tool_status_text(payload) == 'Searching Flight records for "VY3003"…'


def test_select_with_no_values_still_names_the_source():
    payload = {"method": "select", "label": "Flight records", "source": "flights", "arguments": {"values": []}}

    assert tool_status_text(payload) == "Searching Flight records…"


def test_update_never_echoes_the_fields_being_written():
    payload = {"method": "update", "label": "Booking", "source": "booking", "arguments": {"fields": {"pnr": "ABC123"}}}

    assert tool_status_text(payload) == "Updating Booking…"


def test_falls_back_to_the_source_name_when_there_is_no_label():
    payload = {"method": "select", "label": None, "source": "flights", "arguments": {"values": ["VY3003"]}}

    assert tool_status_text(payload) == 'Searching flights for "VY3003"…'
