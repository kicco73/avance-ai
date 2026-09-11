"""Job primitives: the generic Job classes any piece of background work
derives from — plain immediate work with no hibernation story (a
cross-project wake-up, an outgoing mail) — plus the broadcaster stub
tests use. What *runs* jobs lives elsewhere: the queue stays here
(jobs.job_queue) but is deliberately not exported — private to
scheduler.SchedulerService (the only door for platform jobs) and
testing.TestingService's own throttled queue. What *schedules* jobs (the
Scheduler contract and its implementations) and the one Job subclass
that can be hibernated to survive a restart (Task) both live in the
scheduler/ package instead — neither is a primitive every background
task derives from, only the ones meant to be scheduled. Import any of
these from its own module only when building one of those services (or
a test of the primitives themselves)."""
from .job import Job, DependentJob, CancelableJob

__all__ = [
    "Job",
    "DependentJob",
    "CancelableJob",
]
