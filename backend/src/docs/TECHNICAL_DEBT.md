# Technical debt

Not a wishlist and not a design doc: a place to name a real, load-bearing
compromise once, with what it costs and where it lives, so the next
person deciding whether to add to it inherits the fact instead of having
to re-derive it. A line here is removed the moment the reason for it
stops being true.

## The database is committed to SQLite, structurally, not incidentally

`Db` (`db/db.py`) is peewee throughout its query layer — every mixin
(`drive.py`, `users.py`, `sessions.py`, `projects.py`'s own archive
methods, …) is portable ORM code, on purpose. Three things underneath it
are not, and can't be made so by writing them differently, because they
depend on behaviour peewee has no cross-database primitive for:

- **`db/migration.py` — 34 raw-SQL call sites.** `SchemaMigrator` decides
  what changed by reading `sqlite_master`/`PRAGMA table_info`/
  `PRAGMA index_list`/`PRAGMA foreign_key_list` directly, and when a
  column's constraint changes it rebuilds the table by hand — rename,
  create the new shape, copy the rows, drop the old one
  (`_rebuild_table`). That dance exists because SQLite itself has no
  `ALTER TABLE` for a constraint change; Postgres/MySQL do, natively.
  This file's whole job is standing in for that gap.
- **`db/db.py` — 23 raw-SQL call sites.** Index-corruption repair
  (`PRAGMA integrity_check`/`REINDEX`), the two GC trigger groups (File,
  and now Drive — see below), and backup/restore, which treats the
  entire database as one file: `export_backup` is `VACUUM INTO`,
  `restore_backup` replaces the file outright. "Backup" is a filesystem
  operation here, not a query.
- **`db/projects.py` — 2 raw-SQL call sites.** A `PRAGMA foreign_keys`
  toggle around `rename_project_id`, needed because SQLite enforces FKs
  on `UPDATE` too and every FK-bearing table's own `project_id` column
  has to move in the same transaction as `Project.id`.

Changing engine means rewriting `db/migration.py` from its own premise
up, and replacing the file-based backup with something engine-native.
Nothing about the Drive feature below made this truer than it already
was — it lands in the one file (`db.py`) that was already the exception,
for the same reason the File GC trigger is there.

## Conditional cascade has no ORM primitive, on any engine

A plain `ForeignKeyField(..., on_delete='CASCADE')` cannot say "cascade
this delete, but only when a sibling column of the parent row equals a
value" — that isn't a SQLite gap, it's a plain-FK gap on every relational
database: `ON DELETE` actions read only the FK relationship itself, never
another column. A trigger is the only mechanism that can, on SQLite,
Postgres, or MySQL alike — just in each one's own DDL dialect, which is
exactly why peewee (or any ORM) has no `Trigger` class to wrap it.

`Db._create_file_gc_triggers` is the other case of the same shape:
`File` is content-addressed (`File.hash`, shared by whichever `Archive`
or `Drive` row happens to hash to the same `(content, content_type)`),
so deleting one referencing row must not delete the `File` row while
another reference to the same hash still exists — a plain
`on_delete='RESTRICT'` on `Archive.hash` and `Drive.hash` stops the
wrong delete, but reclaiming an orphaned `File` row still needs the
`AFTER DELETE`/`AFTER UPDATE OF "hash"` triggers on *both* tables,
each checking the other for a surviving reference before the row goes.

`Db._create_drive_gc_triggers` (`db/db.py`, alongside
`_create_file_gc_triggers`) is one such trigger:
`Drive.session_id` carries the session a file was written in, if any
(`None` for a `task.defer`red write — see `PROJECT_SPECS.md` §5.4's
`drive.*`), and a `BEFORE DELETE ON CoreSession WHEN OLD.type = 'test'`
trigger sweeps a test session's own drive rows the moment its
`CoreSession` row is deleted — through *any* of the several paths that
delete one (`delete_chat_session`, `delete_sessions_by_username_and_type`
— a new test session superseding an old one, the most common case in
practice — `reset_project_for_user`, …), not just the ones an
application-level hook happens to call. A `FOREIGN KEY ... SET NULL`
alone would have been enough to keep a live session's deletion from ever
touching the drive (it still is the column's own `ON DELETE` action,
harmless once the trigger has already deleted the row for a test
session); it was not enough on its own to make deletion automatic,
because "automatic" here means "for every present and future path a
`CoreSession` row can disappear from," which only the database itself,
not an enumerable list of call sites, can promise.

One SQLite-specific ordering fact worth recording because it is not
obvious and cost real debugging time here: `ON DELETE SET NULL` and an
`AFTER DELETE` trigger both fire off the same `DELETE`, and SQLite runs
the FK action first — a `WHERE session_id = OLD.id` inside an `AFTER
DELETE` trigger matches nothing, because the FK action already nulled
that column before the trigger body ran. The fix is `BEFORE DELETE`,
which runs while the row (and the FK columns still pointing at it) are
intact.

## An on-exit's visual effects outlive a turn that fails

A manual action or a choice runs the transition, its `on-exit` and the
target state's reply in one `AtomicTurnTransaction`
(`turn/input_processor.py`, `manual_action`/`choice`). When the provider
fails — busy, 503, a stall — the transaction is discarded: no transition,
no env write, no `chat.write`, no `task:`. The session stays in the
source state with its buttons, and pressing again re-runs the `on-exit`
from the untouched env, so a counter is incremented once, not twice
(`tests/test_turn_service_provider_busy_on_transition.py`).

What the transaction does not hold is the presentation `chat.*` calls:
`chat.chart`/`chat.progress` publish on the call
(`tracking/actuators/chat_namespace.py`), and `clear`/`celebrate`/`show`/
`notify` are pushed at the end of the `on-exit`, before the model is
called (`tracking/tracking_engine.py`, `apply_action_env`). The user sees
them, then the error, then sees them again on the retry. Nothing is
wrong in the env; the cost is visual.

Holding them until the commit is not an option: the commit comes after
the reply, and a deferred `chat.clear()` would wipe it. Running the
action after the model answers is not one either: the action completes
before the model because the model may read what it wrote.

The candidate, not built: the transaction queues those frames like it
queues `task:`, releases them on the reply's first chunk, releases what
is left at commit (a `system` target state has no chunk), and drops them
on discard. Order stays clear → chart → text. It does not cover a
failure after the first chunk, and in a state with tools the effects
wait for the tool rounds.

The data, from `AiUsage` in the dev database, counted from the first row
with `time_to_first_chunk` (2026-09-22 08:18; earlier rows predate the
column). A failure row records that time too when a chunk had arrived
(`ai/ai_service.py`, `_UsageTap.record_failure`):

- 211 calls, 19 failed (≈9%), all `unavailable`, all Gemini flash-lite
  (17 on 3.5, 2 on 3.1); Mistral 16 calls, 0 failures.
- All 19 failures happened before the first chunk; 0 of the 189 calls
  that received a chunk failed afterwards (upper bound ≈1.6% at 95%, rule
  of three).

Enough to say the candidate would have covered every failure seen; not
enough to size the gain: a day and a half of dev traffic, one model,
failures in bursts, and no count of how many fell on a state change.
Parked until there is production data.
