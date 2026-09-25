from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from db.models import AiUsage

pytestmark = pytest.mark.contract


def test_ai_costs_prices_each_day_s_tokens_per_provider_in_euros(client):
    today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    AiUsage.create(provider_label="fake/fake-model", timestamp=yesterday, input_tokens=1_000_000, output_tokens=0)
    AiUsage.create(
        provider_label="fake/fake-model", timestamp=today,
        input_tokens=500_000, output_tokens=1_000_000, thoughts_tokens=250_000,
    )
    AiUsage.create(provider_label="gone/old-model", timestamp=today, input_tokens=10, output_tokens=10)

    body = client.get("/api/core/settings/services/ai-costs").json()

    assert body["currency"] == "EUR"
    assert body["history"] == [
        {"timestamp": f"{yesterday:%Y-%m-%d}T00:00:00Z", "values": {"fake/fake-model": 1.0}},
        {"timestamp": f"{today:%Y-%m-%d}T00:00:00Z", "values": {"fake/fake-model": 0.5 + 2.0 + 1.0}},
    ]
    assert body["unpriced_providers"] == ["gone/old-model"]


def test_ai_costs_leave_out_days_older_than_asked(client):
    AiUsage.create(
        provider_label="fake/fake-model", timestamp=datetime.utcnow() - timedelta(days=40),
        input_tokens=1_000_000, output_tokens=0,
    )

    assert client.get("/api/core/settings/services/ai-costs", params={"days": 30}).json()["history"] == []
