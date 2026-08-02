from __future__ import annotations


class BenchmarkNormalizer(object):
    """Converts benchmark quantities to the common 0..100 scale."""

    @staticmethod
    def clamp(value: float) -> float:
        return max(0.0, min(100.0, float(value)))

    @classmethod
    def exact_match(cls, matches: int, samples: int) -> float:
        if samples <= 0:
            return 0.0
        return cls.clamp(matches / samples * 100.0)

    @classmethod
    def signal_agreement(cls, actual: float | None, expected: float) -> float:
        if actual is None:
            return 0.0
        error = abs(float(actual) - float(expected))
        return cls.clamp(100.0 - error)

    @classmethod
    def delay_to_quality(cls, absolute_delay_seconds: float, max_delay_seconds: float) -> float:
        if max_delay_seconds <= 0:
            return 100.0 if absolute_delay_seconds <= 0 else 0.0
        return cls.clamp(100.0 * (1.0 - absolute_delay_seconds / max_delay_seconds))

    @classmethod
    def absolute_error_to_quality(cls, absolute_error: float, max_error: float) -> float:
        if max_error <= 0:
            return 100.0 if absolute_error <= 0 else 0.0
        return cls.clamp(100.0 * (1.0 - absolute_error / max_error))

    @classmethod
    def standard_deviation_to_stability(cls, standard_deviation: float, max_standard_deviation: float) -> float:
        return cls.absolute_error_to_quality(standard_deviation, max_standard_deviation)

    @classmethod
    def bias_to_consistency(cls, signed_mean_error: float, max_absolute_error: float) -> float:
        return cls.absolute_error_to_quality(abs(signed_mean_error), max_absolute_error)
