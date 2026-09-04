from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .models import Task

TASK_STATUSES = ('pending', 'dispatched', 'done', 'failed', 'canceled')
TASK_TERMINAL_STATUSES = ('done', 'failed', 'canceled')


def _to_naive_utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when
    return when.astimezone(timezone.utc).replace(tzinfo=None)


class TaskMixin:
    """The Task table as a queue — see job/persisted_scheduler.py, its
    only writer. Nothing here knows what a task *does*, only its row;
    the two operations that matter for correctness are claim_due_task
    (an atomic pending -> dispatched hand-off, so two schedulers over
    the same database never run one row twice) and the cascades
    declared on the model (a deleted user/project takes its rows along)."""

    @staticmethod
    def _task_to_dict(row: Task) -> dict[str, Any]:
        return {
            'id': row.id,
            'key': row.key,
            'type': row.type,
            'username': row.user_id,
            'project_id': row.project_id,
            'run_at': row.run_at.replace(tzinfo=timezone.utc),
            'payload': json.loads(row.payload),
            'ui_label': row.ui_label,
            'ui_description': row.ui_description,
            'status': row.status,
            'error': row.error,
            'created_at': row.created_at.replace(tzinfo=timezone.utc) if row.created_at else None,
            'dispatched_at': row.dispatched_at.replace(tzinfo=timezone.utc) if row.dispatched_at else None,
            'settled_at': row.settled_at.replace(tzinfo=timezone.utc) if row.settled_at else None,
        }

    def create_task(
        self, key: str, type: str, username: str, project_id: str, run_at: datetime, payload: dict[str, Any],
        ui_label: str, ui_description: str,
    ) -> int:
        row = Task.create(
            key=key, type=type, user=username, project=project_id, run_at=_to_naive_utc(run_at),
            payload=json.dumps(payload), ui_label=ui_label, ui_description=ui_description, status='pending',
        )
        return row.id

    def upsert_task(
        self, key: str, type: str, username: str, project_id: str, run_at: datetime, payload: dict[str, Any],
        ui_label: str, ui_description: str,
    ) -> None:
        """create_task(), but a settled/canceled row already sitting under
        this same deterministic key (an earlier cycle of the same id — see
        Task.make_key) is overwritten and reset to pending in place, rather
        than raising on the key's own UNIQUE constraint. create_task()
        itself stays a strict insert: with today's random per-submission
        keys, a collision there is a real uuid4 collision worth raising on,
        never something to mask."""
        changed = Task.update(
            type=type, user=username, project=project_id, run_at=_to_naive_utc(run_at),
            payload=json.dumps(payload), ui_label=ui_label, ui_description=ui_description,
            status='pending', error=None, dispatched_at=None, settled_at=None,
        ).where(Task.key == key).execute()
        if changed == 0:
            self.create_task(key, type, username, project_id, run_at, payload, ui_label, ui_description)

    def get_task(self, key: str) -> dict[str, Any] | None:
        row = Task.get_or_none(Task.key == key)
        return self._task_to_dict(row) if row is not None else None

    def list_tasks(
        self, username: str | None = None, project_id: str | None = None, status: str | None = None,
        order: str = 'asc',
    ) -> list[dict[str, Any]]:
        if status is not None and status not in TASK_STATUSES:
            raise ValueError(f"'{status}' is not a valid task status — expected one of {TASK_STATUSES}.")
        if order not in ('asc', 'desc'):
            raise ValueError(f"'{order}' is not a valid order — expected 'asc' or 'desc'.")
        query = Task.select()
        if username is not None:
            query = query.where(Task.user == username)
        if project_id is not None:
            query = query.where(Task.project == project_id)
        if status is not None:
            query = query.where(Task.status == status)
        ordering = (Task.run_at, Task.id) if order == 'asc' else (Task.run_at.desc(), Task.id.desc())
        return [self._task_to_dict(row) for row in query.order_by(*ordering)]

    def next_task_due_at(self) -> datetime | None:
        """When the earliest still-pending task is due (UTC), or None."""
        row = Task.select(Task.run_at).where(Task.status == 'pending').order_by(Task.run_at, Task.id).first()
        return row.run_at.replace(tzinfo=timezone.utc) if row is not None else None

    def claim_due_task(self, now: datetime) -> dict[str, Any] | None:
        """Atomically moves the earliest pending task due by `now` to
        `dispatched` and returns it — None when nothing is due. The
        UPDATE is guarded on status='pending', so a concurrent claimer
        (another thread, another process) loses cleanly and just retries."""
        now = _to_naive_utc(now)
        while True:
            row = (
                Task.select().where((Task.status == 'pending') & (Task.run_at <= now))
                .order_by(Task.run_at, Task.id).first()
            )
            if row is None:
                return None
            claimed = Task.update(status='dispatched', dispatched_at=now).where(
                (Task.key == row.key) & (Task.status == 'pending')
            ).execute()
            if claimed == 1:
                row.status = 'dispatched'
                row.dispatched_at = now
                return self._task_to_dict(row)

    def reschedule_task(self, key: str, run_at: datetime) -> bool:
        """Moves an already-pending task's run_at in place — True if one
        was found and moved, False if none is pending under this key
        (already dispatched/settled, or never created): the caller's cue
        to create_task() instead rather than silently doing nothing."""
        changed = Task.update(run_at=_to_naive_utc(run_at)).where(
            (Task.key == key) & (Task.status == 'pending')
        ).execute()
        return changed == 1

    def cancel_task(self, key: str) -> bool:
        """pending -> canceled; False when the task was no longer pending
        (already dispatched or settled)."""
        changed = Task.update(status='canceled', settled_at=datetime.utcnow()).where(
            (Task.key == key) & (Task.status == 'pending')
        ).execute()
        return changed == 1

    def settle_task(self, key: str, status: str, error: str | None = None) -> None:
        if status not in TASK_TERMINAL_STATUSES:
            raise ValueError(f"'{status}' is not a terminal task status — expected one of {TASK_TERMINAL_STATUSES}.")
        Task.update(status=status, error=error, settled_at=datetime.utcnow()).where(Task.key == key).execute()

    def requeue_stale_dispatched_tasks(self, older_than: datetime) -> list[str]:
        """Recovery: every row claimed before `older_than` and never
        settled belongs to a process that died mid-run — it goes back to
        pending, to be claimed again. Rows claimed more recently are
        assumed to be running somewhere (this process, or another
        instance over the same database) and are left alone; that is
        what makes a scheduler's lease the only window in which a task
        can be run twice. Returns the requeued keys, for the log."""
        older_than = _to_naive_utc(older_than)
        stale = Task.select(Task.key).where(
            (Task.status == 'dispatched') & (Task.dispatched_at.is_null() | (Task.dispatched_at < older_than))
        )
        keys = [row.key for row in stale]
        if keys:
            Task.update(status='pending', dispatched_at=None).where(
                Task.key.in_(keys) & (Task.status == 'dispatched')
            ).execute()
        return keys
