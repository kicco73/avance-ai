from __future__ import annotations

import json

from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.dto import BenchmarkMetricResult


def _job_result(job: CancelableJob) -> dict:
    """job.result is only read here once every dependency Job.is_done()
    (guaranteed by the job queue before a dependent's _compute() ever
    runs) — never actually None at this point, just typed that way for
    a job that hasn't finished yet."""
    assert job.result is not None, f"{job.key}: dependency finished without a result"
    return json.loads(job.result)


def _serialize_metric_result(result: BenchmarkMetricResult) -> dict:
    return {
        'name': result.name,
        'value': result.value,
        'mean': result.mean,
        'median': result.median,
        'standard_deviation': result.standard_deviation,
        'minimum': result.minimum,
        'maximum': result.maximum,
        'sample_count': result.sample_count,
        'distribution': list(result.distribution),
        'components': result.components,
    }
