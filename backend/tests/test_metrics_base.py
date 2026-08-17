from __future__ import annotations

import pytest

from metrics.metrics_framework.metrics.base import BaseMetric


@pytest.mark.regression
def test_result_clamps_the_value_to_0_100():
    assert BaseMetric.result("x", 150).value == 100.0
    assert BaseMetric.result("x", -10).value == 0.0
    assert BaseMetric.result("x", 42).value == 42.0


@pytest.mark.regression
def test_result_defaults_components_to_an_empty_dict():
    result = BaseMetric.result("x", 10)
    assert result.components == {}


@pytest.mark.regression
def test_result_stamps_a_calculated_at_timestamp():
    result = BaseMetric.result("x", 10)
    assert result.calculated_at is not None


@pytest.mark.contract
def test_base_metric_name_is_not_implemented():
    with pytest.raises(NotImplementedError):
        BaseMetric().name


@pytest.mark.contract
def test_base_metric_ui_label_is_not_implemented():
    with pytest.raises(NotImplementedError):
        BaseMetric().ui_label


@pytest.mark.contract
def test_base_metric_ui_description_is_not_implemented():
    with pytest.raises(NotImplementedError):
        BaseMetric().ui_description


@pytest.mark.contract
def test_base_metric_calculate_is_not_implemented():
    with pytest.raises(NotImplementedError):
        BaseMetric().calculate(None)
