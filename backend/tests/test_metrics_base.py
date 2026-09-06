from __future__ import annotations

import pytest

from metrics.metrics_framework.metrics.base import BaseMetric


@pytest.mark.regression
def test_result_clamps_the_value_to_0_100_defaulting_components_to_empty_and_stamping_a_timestamp():
    assert BaseMetric.result("x", 150).value == 100.0
    assert BaseMetric.result("x", -10).value == 0.0

    result = BaseMetric.result("x", 42)
    assert result.value == 42.0
    assert result.components == {}
    assert result.calculated_at is not None


@pytest.mark.contract
@pytest.mark.parametrize("member", ["name", "ui_label", "ui_description", "calculate"])
def test_every_abstract_member_of_the_base_metric_raises_not_implemented(member):
    metric = BaseMetric()

    with pytest.raises(NotImplementedError):
        metric.calculate(None) if member == "calculate" else getattr(metric, member)
