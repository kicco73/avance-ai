from __future__ import annotations

from datetime import datetime

from peewee import AutoField, BlobField, BooleanField, CharField, CompositeKey, DateTimeField, ForeignKeyField, IntegerField, Model, Proxy, TextField

database = Proxy()


class BaseModel(Model):

    class Meta:
        database = database

class Project(BaseModel):
    name = CharField(primary_key=True)
    revision = IntegerField(null=False, default=0)
    published_revision = IntegerField(null=True)

class ChatSession(BaseModel):
    id = AutoField()
    username = CharField()
    project_name = ForeignKeyField(Project, field='name', column_name='project_name', backref='chat_sessions')
    source = CharField(default='native')
    # Optional, freeform — never shown for a native session unless the
    # user someday gets a way to set one (none exists yet); an imported
    # one always gets the uploaded transcript's own filename (see
    # SessionImportManager.import_transcript). Shown in the Sessions
    # panel's own badge in place of end_state, when set.
    title = CharField(null=True)
    # The project's own published_revision at the moment this session was
    # created — never touched again after (see Db.save_project_files's own
    # fork-on-first-edit-after-publish: a later fork must never silently
    # reinterpret an already-created session's own state keys against a
    # revision it never actually ran against).
    project_revision = IntegerField(null=False)
    datetime_start = DateTimeField(null=True)
    datetime_end = DateTimeField(null=True)
    start_state = CharField(null=True)
    end_state = CharField(null=True)
    # Explicitly set by a domain expert (see "Label sessions" view's own
    # "Mark done" button, ChatService.mark_session_labeled) — the single
    # source of truth for whether a session counts as reviewed, replacing
    # the old heuristic (any Tracking row in it carries an expert
    # annotation) that used to derive this implicitly. A toggle, not a
    # one-way flag: pressing "Mark done" again clears it back to False.
    labeled = BooleanField(default=False)

    class Meta:
        indexes = ((('username', 'project_name', 'datetime_start', 'datetime_end'), False), (('username', 'project_name', 'start_state', 'end_state'), False))

class Message(BaseModel):
    id = AutoField()
    role = CharField()
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow, null=True)
    audio_text = TextField(null=True)
    session = ForeignKeyField(ChatSession, null=False, backref='messages', on_delete='CASCADE')

class Settings(BaseModel):
    user = CharField(primary_key=True)
    project = CharField()

class Tracking(BaseModel):
    id = AutoField()
    session = ForeignKeyField(ChatSession, null=False, backref='tracking', on_delete='CASCADE')
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)
    env = TextField(null=True)
    action_env = TextField(null=True)
    expected_state = CharField(null=True)
    expected_values = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)
    message = ForeignKeyField(Message, null=True, backref='tracking_row', on_delete='SET NULL')

class Archive(BaseModel):
    id = AutoField()
    project_name = ForeignKeyField(Project, field='name', column_name='project_name', backref='archives')
    archive_name = CharField(index=True, null=False)
    revision = IntegerField(null=False, default=0)
    content = BlobField(null=False)
    content_type = CharField(null=False)

    class Meta:
        # One row per revision — a published revision's own rows are never
        # updated in place again (see Db.save_project_files's fork step),
        # so (project_name, archive_name) alone can no longer be unique.
        indexes = ((('project_name', 'archive_name', 'revision'), True),)

class StateRemap(BaseModel):
    """An administrative fact about a published revision, not a
    conversation event (never goes in Tracking) — independent of how many
    sessions/users exist, ready for a future multi-user resolution that
    isn't built yet (see ProjectService.get_active_automaton_and_state).
    Flattened on every write (see Db.write_state_remap) so resolving a key
    is always a single lookup, never a chain, regardless of how many
    publications have passed since it was first remapped."""
    project_name = CharField()
    old_key = CharField()
    new_key = CharField()

    class Meta:
        primary_key = CompositeKey('project_name', 'old_key')

class Job(BaseModel):
    id = AutoField()
    kind = CharField()
    reference_id = IntegerField(null=True)
    status = CharField()
    created_at = DateTimeField(index=True, default=datetime.utcnow)
    finished_at = DateTimeField(null=True)
    error = TextField(null=True)
    result = TextField(null=True)
    progress_current = IntegerField(default=0)
    progress_total = IntegerField(default=0)

class SessionSummary(BaseModel):
    id = AutoField()
    # unique=True: at most one summary per session, ever — its own
    # existence is what stops SessionSummaryManager.check_for_closed_
    # sessions from re-queuing the same session a second time.
    session = ForeignKeyField(ChatSession, unique=True, backref='summary', on_delete='CASCADE')
    # Null until the job completes — see SessionSummaryManager's own work.
    content = TextField(null=True)

class BenchmarkRun(BaseModel):
    id = AutoField()
    username = CharField()
    project_name = CharField(index=True)
    # None means "every labeled session of the project", never a single
    # unresolved session — same dual as BenchmarkCalculator(session_id=
    # None|int) (see metrics/metrics_framework/benchmark_metrics/calculator.py).
    session = ForeignKeyField(ChatSession, null=True, backref='benchmark_runs', on_delete='CASCADE')
    strategy = CharField()
    # The project's own draft revision at the moment this run was
    # created — captured once, up front, regardless of which revision is
    # published (see ChatSession.project_revision, same idea).
    project_revision = IntegerField(null=False)
    # Only ever set for strategy='batch' — stays null for 'turn_by_turn'.
    batch_segments = IntegerField(null=True)
    ai_model_snapshot = TextField(null=True)
    results = TextField(null=True)

    class Meta:
        indexes = ((('username', 'project_name'), False),)

class BenchmarkRunObservation(BaseModel):
    """A replay's own signal snapshot/transition — the exact same shape
    Tracking carries for production, but on its own table: a replay must
    never be mistaken for (or overwrite) real conversation data. See
    tracking/tracking_engine.py's BenchmarkRunObservationSink."""
    id = AutoField()
    run = ForeignKeyField(BenchmarkRun, null=False, backref='observations', on_delete='CASCADE')
    session = ForeignKeyField(ChatSession, null=False, backref='benchmark_run_observations', on_delete='CASCADE')
    message = ForeignKeyField(Message, null=True, backref='benchmark_observations', on_delete='SET NULL')
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)

    class Meta:
        indexes = ((('run', 'session'), False),)

class History(BaseModel):
    id = AutoField()
    user_id = CharField(index=True, null=False)
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    kind = CharField(null=False)
    seq = IntegerField(null=False)
    content = BlobField(null=False)

    class Meta:
        indexes = ((('user_id', 'project_name', 'archive_name', 'kind', 'seq'), True),)

DEFAULT_USER = "user"
