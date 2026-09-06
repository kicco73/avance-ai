from __future__ import annotations

import pytest

from metrics.metrics_framework.normalization import Normalizer

pytestmark = pytest.mark.regression


def test_clamp_bounds_the_value_to_0_100_or_to_custom_bounds():
    assert Normalizer.clamp(-10) == 0.0
    assert Normalizer.clamp(150) == 100.0
    assert Normalizer.clamp(42) == 42.0
    assert Normalizer.clamp(5, minimum=10, maximum=20) == 10.0
    assert Normalizer.clamp(25, minimum=10, maximum=20) == 20.0


def test_linear_maps_low_high_onto_0_100_clamping_outside_it_and_degenerating_into_a_step_function():
    assert Normalizer.linear(0, 0, 10) == 0.0
    assert Normalizer.linear(5, 0, 10) == 50.0
    assert Normalizer.linear(10, 0, 10) == 100.0
    assert Normalizer.linear(-5, 0, 10) == 0.0
    assert Normalizer.linear(15, 0, 10) == 100.0

    # high <= low can't be interpolated — treated as a hard threshold at
    # `high`: below it scores 0, at or above it scores 100.
    assert Normalizer.linear(5, 10, 10) == 0.0
    assert Normalizer.linear(10, 10, 10) == 100.0
    assert Normalizer.linear(15, 10, 10) == 100.0


def test_inverse_linear_is_the_complement_of_linear():
    assert Normalizer.inverse_linear(0, 0, 10) == 100.0
    assert Normalizer.inverse_linear(5, 0, 10) == 50.0
    assert Normalizer.inverse_linear(10, 0, 10) == 0.0


def test_ratio_scales_against_a_reference_clamping_rather_than_extrapolating_and_scoring_zero_without_one():
    assert Normalizer.ratio(0, 10) == 0.0
    assert Normalizer.ratio(5, 10) == 50.0
    assert Normalizer.ratio(20, 10) == 100.0
    assert Normalizer.ratio(5, 0) == 0.0
    assert Normalizer.ratio(5, -3) == 0.0
