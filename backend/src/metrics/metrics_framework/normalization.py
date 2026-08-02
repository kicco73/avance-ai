from __future__ import annotations


class Normalizer(object):
    @staticmethod
    def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
        return max(minimum, min(maximum, value))

    @staticmethod
    def linear(value: float, low: float, high: float) -> float:
        if high <= low:
            return 100.0 if value >= high else 0.0
        return Normalizer.clamp((value - low) / (high - low) * 100.0)

    @staticmethod
    def inverse_linear(value: float, low: float, high: float) -> float:
        return 100.0 - Normalizer.linear(value, low, high)

    @staticmethod
    def ratio(value: float, reference: float) -> float:
        if reference <= 0:
            return 0.0
        return Normalizer.clamp(value / reference * 100.0)
