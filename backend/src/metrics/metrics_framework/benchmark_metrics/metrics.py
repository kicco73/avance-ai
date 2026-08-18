from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median, pstdev

from .dto import BenchmarkConfiguration, BenchmarkMetricResult, BenchmarkObservation
from .interfaces import BenchmarkMetric
from .normalization import BenchmarkNormalizer


class _Statistics(object):
    @staticmethod
    def result(
        name: str,
        values: list[float],
        *,
        components: dict[str, float] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> BenchmarkMetricResult:
        if not values:
            return BenchmarkMetricResult(
                name=name,
                value=0.0,
                sample_count=0,
                components=components or {},
                metadata=metadata or {},
                calculated_at=datetime.now(timezone.utc),
            )
        return BenchmarkMetricResult(
            name=name,
            value=mean(values),
            mean=mean(values),
            median=median(values),
            standard_deviation=pstdev(values) if len(values) > 1 else 0.0,
            minimum=min(values),
            maximum=max(values),
            sample_count=len(values),
            components=components or {},
            metadata=metadata or {},
            calculated_at=datetime.now(timezone.utc),
        )


def _state_accuracy_values(observations: tuple[BenchmarkObservation, ...], expected_transition: bool) -> list[float]:
    return [
        float(o.state_agreement)
        for o in observations
        if o.expected_transition is expected_transition and o.state_agreement is not None
    ]


class StateAccuracyMetric(BenchmarkMetric):
    @property
    def name(self) -> str:
        return "state_accuracy"

    @property
    def ui_label(self) -> str:
        return "State Accuracy"

    @property
    def ui_description(self) -> str:
        return "Percentage of expert-annotated points where the system reached the expected state."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        values = [o.state_agreement for o in observations if o.state_agreement is not None]
        return _Statistics.result(self.name, [float(v) for v in values], metadata={"unit": "percent"})


class StateAccuracyStableMetric(BenchmarkMetric):
    @property
    def name(self) -> str:
        return "state_accuracy_stable"

    @property
    def ui_label(self) -> str:
        return "State Accuracy (Stable Points)"

    @property
    def ui_description(self) -> str:
        return "Percentage of expert-annotated points where no state change was expected and the system correctly stayed in the current state."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        values = _state_accuracy_values(observations, expected_transition=False)
        return _Statistics.result(self.name, values, metadata={"unit": "percent"})


class StateAccuracyTransitionMetric(BenchmarkMetric):
    @property
    def name(self) -> str:
        return "state_accuracy_transition"

    @property
    def ui_label(self) -> str:
        return "State Accuracy (Expected Transitions)"

    @property
    def ui_description(self) -> str:
        return "Percentage of expert-annotated points where a state change was expected and the system reached the correct new state."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        values = _state_accuracy_values(observations, expected_transition=True)
        return _Statistics.result(self.name, values, metadata={"unit": "percent"})


class SignalAccuracyMetric(BenchmarkMetric):
    @property
    def name(self) -> str:
        return "signal_accuracy"

    @property
    def ui_label(self) -> str:
        return "Signal Accuracy"

    @property
    def ui_description(self) -> str:
        return "How close the system's signal values are to the expert's annotated expected values."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        values = [v for o in observations for v in o.signal_agreements.values()]
        per_signal: dict[str, list[float]] = {}
        for observation in observations:
            for name, value in observation.signal_agreements.items():
                per_signal.setdefault(name, []).append(value)
        components = {
            name: mean(values_for_signal)
            for name, values_for_signal in per_signal.items()
            if values_for_signal
        }
        return _Statistics.result(self.name, values, components=components, metadata={"unit": "percent"})


class TransitionResponsivenessMetric(BenchmarkMetric):
    @property
    def name(self) -> str:
        return "transition_responsiveness"

    @property
    def ui_label(self) -> str:
        return "Transition Responsiveness"

    @property
    def ui_description(self) -> str:
        return "How close in message position and time expected state transitions occurred to when they actually did."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        values = [
            float(o.transition_responsiveness)
            for o in observations
            if o.transition_responsiveness is not None
        ]
        return _Statistics.result(self.name, values, metadata={"unit": "percent"})


class BenchmarkAccuracyMetric(BenchmarkMetric):
    @property
    def name(self) -> str:
        return "benchmark_accuracy"

    @property
    def ui_label(self) -> str:
        return "Benchmark Accuracy"

    @property
    def ui_description(self) -> str:
        return "Overall agreement with expert expectations — the mean of state accuracy, signal accuracy, and transition responsiveness."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        state_values = [o.state_agreement for o in observations if o.state_agreement is not None]
        signal_values = [v for o in observations for v in o.signal_agreements.values()]
        transition_values = [
            o.transition_responsiveness
            for o in observations
            if o.transition_responsiveness is not None
        ]
        components: dict[str, float] = {}
        component_values: list[float] = []
        for name, values in (
            ("state_accuracy", state_values),
            ("signal_accuracy", signal_values),
            ("transition_responsiveness", transition_values),
        ):
            if values:
                score = mean(float(v) for v in values)
                components[name] = score
                component_values.append(score)
        return _Statistics.result(self.name, component_values, components=components, metadata={"unit": "percent"})


class BenchmarkStabilityMetric(BenchmarkMetric):
    # Dispersion of the project's own error only means something across
    # the whole cross-session benchmark, not one session alone.
    scope = frozenset({"all_sessions"})

    @property
    def name(self) -> str:
        return "benchmark_stability"

    @property
    def ui_label(self) -> str:
        return "Benchmark Stability"

    @property
    def ui_description(self) -> str:
        return "How consistent (low-dispersion) the project's behavior is relative to the benchmark, independent of its accuracy."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        # Stability is calculated from error distributions. For normalized
        # accuracy values, 0..100 is the complete observable error range.
        error_groups: dict[str, list[float]] = {
            "state_error": [100.0 - float(o.state_agreement) for o in observations if o.state_agreement is not None],
            "signal_error": [
                100.0 - float(value)
                for o in observations
                for value in o.signal_agreements.values()
            ],
            "transition_error": [
                100.0 - float(o.transition_responsiveness)
                for o in observations
                if o.transition_responsiveness is not None
            ],
        }
        stability_components: dict[str, float] = {}
        for name, errors in error_groups.items():
            sd = pstdev(errors) if len(errors) > 1 else 0.0
            stability_components[name] = BenchmarkNormalizer.standard_deviation_to_stability(sd, 50.0)
        values = list(stability_components.values())
        return _Statistics.result(self.name, values, components=stability_components, metadata={"unit": "percent"})


class BenchmarkConsistencyMetric(BenchmarkMetric):
    # Systematic directional bias only means something across the whole
    # cross-session benchmark, not one session alone.
    scope = frozenset({"all_sessions"})

    def __init__(self, configuration: BenchmarkConfiguration | None = None) -> None:
        self._configuration = configuration or BenchmarkConfiguration()

    @property
    def name(self) -> str:
        return "benchmark_consistency"

    @property
    def ui_label(self) -> str:
        return "Benchmark Consistency"

    @property
    def ui_description(self) -> str:
        return "Absence of a systematic directional bias in signal values or transition timing — 100 means no consistent over/under-estimation."

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        # 100 means no systematic directional error. Signal bias is measured
        # from signed actual-expected differences; timing bias from signed
        # transition delays. All exposed scores are normalized to 0..100.
        signal_biases: dict[str, list[float]] = {}
        for observation in observations:
            for name, error in observation.signal_signed_errors.items():
                signal_biases.setdefault(name, []).append(error)

        signal_consistency = {
            name: BenchmarkNormalizer.bias_to_consistency(mean(errors), 100.0)
            for name, errors in signal_biases.items()
        }
        timing_delays = [
            float(o.time_delay_seconds)
            for o in observations
            if o.time_delay_seconds is not None
        ]
        timing_consistency = (
            BenchmarkNormalizer.bias_to_consistency(
                mean(timing_delays),
                self._configuration.max_session_duration_in_minutes * 60.0,
            )
            if timing_delays
            else None
        )
        components = dict(signal_consistency)
        if timing_consistency is not None:
            components["transition_timing_consistency"] = timing_consistency
        values = list(components.values())
        return _Statistics.result(
            self.name,
            values,
            components=components,
            metadata={"unit": "percent"},
        )


__all__ = [
    "StateAccuracyMetric",
    "StateAccuracyStableMetric",
    "StateAccuracyTransitionMetric",
    "SignalAccuracyMetric",
    "TransitionResponsivenessMetric",
    "BenchmarkAccuracyMetric",
    "BenchmarkStabilityMetric",
    "BenchmarkConsistencyMetric",
]
