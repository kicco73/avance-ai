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
    draft_edit_count = IntegerField(null=False, default=0)
    # A project is paused when its own build fails, or when any project
    # it references via automaton.* is itself unavailable. paused_reason
    # is null exactly when is_paused is False.
    is_paused = BooleanField(default=False)
    paused_reason = TextField(null=True)
    # An operator's own explicit override, independent of the automatic
    # is_paused mechanism — whenever True, recompute_availability forces
    # is_paused True too, so nothing un-pauses it but a manual resume.
    manually_paused = BooleanField(default=False)
    # The project's own declared `project:` section, kept in sync on
    # every save rather than parsed from index.yml on demand. project_id
    # is what *other* projects reach this one as through automaton.*.
    project_id = CharField(null=True, unique=True)
    ui_label = TextField(null=True)
    ui_description = TextField(null=True)

    class Meta:
        table_name = 'Project'

class User(BaseModel):
    id = CharField(primary_key=True)
    # "google", etc. — which AuthProvider verified this account. Nullable
    # along with provider_user_id/name: UserMixin's get_active_project_name/
    # set_active_project_name/clear_active_project_name still take a bare
    # `user: str` (resolved against `email`, see db/users.py) rather than a
    # real FK — set_active_project_name's own create-fallback, for a user
    # with no User row yet, has no provider identity to fill these with.
    provider = CharField(null=True)
    # The provider's own opaque id for this account (Google: the "sub"
    # claim) — stable identity, unlike email, which a provider account
    # could in principle change.
    provider_user_id = CharField(null=True)
    email = CharField()
    name = CharField(null=True)
    picture_url = CharField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    last_login = DateTimeField(null=True)
    # Absorbs the old standalone Settings table — its only field, the
    # user's own active project. Nullable: a user with no projects yet
    # still needs a row. on_delete='SET NULL' so deleting the active
    # project never leaves a dangling reference — every user pointing at
    # it just goes back to "no active project" (get_active_project_name
    # picks a fallback the next time it's read).
    active_project = ForeignKeyField(
        Project, field='name', column_name='active_project_id', null=True,
        backref='users_with_active', on_delete='SET NULL',
    )
    role = CharField(default='user')

    class Meta:
        table_name = 'User'
        indexes = ((('provider', 'provider_user_id'), True),)

class ChatSession(BaseModel):
    id = AutoField()
    username = CharField()
    # Nullable: a live session's own creator, when they're a real
    # registered account (see db/sessions.py's create_chat_session) — an
    # imported transcript's synthetic identity (e.g. "Test user 3", see
    # next_test_user_username below) was never a real account, so this
    # stays null for it. `username` above is kept regardless, as the
    # display/lookup identifier every existing query already uses;
    # `user` only backs "erase all my data"'s cascade.
    user = ForeignKeyField(User, field='id', column_name='user_id', null=True, backref='chat_sessions_owned', on_delete='CASCADE')
    project_name = ForeignKeyField(Project, field='name', column_name='project_name', backref='chat_sessions')
    type = CharField(default='live')
    # Optional, freeform — an imported session gets the uploaded
    # transcript's filename to start with; a native one has none until
    # renamed. Shown in the Sessions panel's badge in place of end_state.
    title = CharField(null=True)
    # The project's own published_revision at the moment this session
    # was created — never touched again, so a later fork never silently
    # reinterprets a session's state keys against a revision it never ran against.
    project_revision = IntegerField(null=False)
    datetime_start = DateTimeField(null=True)
    datetime_end = DateTimeField(null=True)
    start_state = CharField(null=True)
    end_state = CharField(null=True)
    # Explicitly set by a domain expert — the single source of truth for
    # whether a session counts as reviewed. A toggle, not a one-way flag:
    # pressing "Mark done" again clears it back to False.
    labeled = BooleanField(default=False)
    # A domain expert's own free-text note on the session as a whole —
    # distinct from Tracking.comment, which is per-message.
    comment = TextField(null=True)
    labeling_revision = IntegerField(null=False, default=0)

    class Meta:
        table_name = 'ChatSession'
        indexes = ((('username', 'project_name', 'datetime_start', 'datetime_end'), False), (('username', 'project_name', 'start_state', 'end_state'), False))

class Message(BaseModel):
    id = AutoField()
    role = CharField()
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow, null=True)
    audio_text = TextField(null=True)
    # The key of the reaction this message received from the other party —
    # the user's own choice on a bot message, or the bot's own choice on a
    # user message (see automaton.Reaction/State.reactions_enabled).
    reaction = TextField(null=True)
    session = ForeignKeyField(ChatSession, null=False, backref='messages', on_delete='CASCADE')

    class Meta:
        table_name = 'Message'

class Tracking(BaseModel):
    id = AutoField()
    session = ForeignKeyField(ChatSession, null=False, backref='tracking', on_delete='CASCADE')
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)
    env = TextField(null=True)
    action_env = TextField(null=True)
    expected_state = CharField(null=True)
    expected_values = TextField(null=True)
    # A domain expert's free-text note on this row's linked message —
    # unlike expected_state/expected_values, never validated against the
    # automaton, just context for whoever reviews this session next.
    comment = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)
    message = ForeignKeyField(Message, null=True, backref='tracking_row', on_delete='SET NULL')

    class Meta:
        table_name = 'Tracking'

class Archive(BaseModel):
    id = AutoField()
    project_name = ForeignKeyField(Project, field='name', column_name='project_name', backref='archives')
    archive_name = CharField(index=True, null=False)
    revision = IntegerField(null=False, default=0)
    content = BlobField(null=False)
    content_type = CharField(null=False)

    class Meta:
        table_name = 'Archive'
        # One row per revision — a published revision's own rows are never
        # updated in place again (see Db.save_project_files's fork step),
        # so (project_name, archive_name) alone can no longer be unique.
        indexes = ((('project_name', 'archive_name', 'revision'), True),)

class StateRemap(BaseModel):
    """An administrative fact about a published revision, not a
    conversation event (never goes in Tracking). Flattened on every
    write so resolving a key is always a single lookup, never a chain."""
    project_name = CharField()
    old_key = CharField()
    new_key = CharField()

    class Meta:
        table_name = 'StateRemap'
        primary_key = CompositeKey('project_name', 'old_key')

class SessionSummary(BaseModel):
    id = AutoField()
    # unique=True: at most one summary per session, ever — its own
    # existence is what stops SessionSummaryManager.check_for_closed_
    # sessions from re-queuing the same session a second time.
    session = ForeignKeyField(ChatSession, unique=True, backref='summary', on_delete='CASCADE')
    # Null until the job completes — see SessionSummaryManager's own work.
    content = TextField(null=True)

    class Meta:
        table_name = 'SessionSummary'

class BenchmarkRun(BaseModel):
    id = AutoField()
    username = CharField(null=True)
    # Nullable for the same reason as ChatSession.user above — this run's
    # own username may be a real registered account or an imported
    # transcript's synthetic identity. Needed as its own FK (not just
    # reachable via session below) because session is itself null for a
    # project-wide aggregate run — see the comment on it just below.
    user = ForeignKeyField(User, field='id', column_name='user_id', null=True, backref='benchmark_runs_owned', on_delete='CASCADE')
    project_name = CharField(index=True)
    # None means "every labeled session of the project", never a single
    # unresolved session — same dual as BenchmarkCalculator(session_id=
    # None|int) (see metrics/metrics_framework/benchmark_metrics/calculator.py).
    session = ForeignKeyField(ChatSession, null=True, backref='benchmark_runs', on_delete='CASCADE')
    strategy = CharField()
    # The project's own draft edit count at the moment this run was
    # created — captured once, up front, regardless of which revision is
    # published (see ChatSession.project_revision, same idea).
    project_draft_edit_count = IntegerField(null=False)
    session_labeling_revision = IntegerField(null=True)
    # Only ever set for strategy='batch' — stays null for 'turn_by_turn'.
    batch_segments = IntegerField(null=True)
    ai_model_snapshot = TextField(null=True)
    results = TextField(null=True)

    class Meta:
        table_name = 'BenchmarkRun'
        indexes = (
            (('username', 'project_name'), False),
            (('session', 'strategy', 'project_draft_edit_count', 'session_labeling_revision'), True),
        )

class BenchmarkRunObservation(BaseModel):
    """A replay's own signal snapshot/transition — the same shape
    Tracking carries for production, but on its own table so a replay
    can never be mistaken for (or overwrite) real conversation data."""
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
        table_name = 'BenchmarkRunObservation'
        indexes = ((('run', 'session'), False),)

class BenchmarkAggregateResult(BaseModel):
    id = AutoField()
    project_name = CharField(index=True)
    revision = IntegerField(null=False)
    project_draft_edit_count = IntegerField(null=False)
    kind = CharField()
    target = CharField(default='')
    strategy = CharField()
    results = TextField(null=False)
    created_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        table_name = 'BenchmarkAggregateResult'
        indexes = (
            (('project_name', 'revision'), False),
            (('project_name', 'revision', 'project_draft_edit_count', 'kind', 'target', 'strategy'), True),
        )

class SystemWarning(BaseModel):
    """A cross-project reference (automaton.<project>.state/env.<key>)
    that resolved to None at runtime instead of raising — one of three
    failure kinds ('project_not_found', 'no_session', 'env_key_not_declared')."""
    id = AutoField()
    # Not nullable: unlike ChatSession/BenchmarkRun's own username, this
    # is always Session().user (see tracking/automaton_namespace.py's
    # AutomatonNamespace) — a real registered account is the only thing
    # ever authenticated enough to reach this code path at all.
    user_id = ForeignKeyField(User, field='id', backref='system_warnings', on_delete='CASCADE')
    project_name = CharField(index=True)
    kind = CharField()
    message = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow)

    class Meta:
        table_name = 'SystemWarning'

class ProjectObserverIndex(BaseModel):
    """Reverse index of automaton.* cross-project references, rebuilt
    from scratch for `observer_project_name` on every index.yml build.
    Queried both as "who observes me" and "who do I depend on"."""
    id = AutoField()
    project_name = CharField(index=True)
    observer_project_name = CharField(index=True)

    class Meta:
        table_name = 'ProjectObserverIndex'
        indexes = ((('project_name', 'observer_project_name'), True),)

class EditHistory(BaseModel):
    """Per-(user, project, file) undo/redo trail for the project editor —
    named EditHistory (not just History) to read unambiguously as project-
    file edit history, not e.g. chat/session history."""
    id = AutoField()
    # Not nullable: always Session().user (see project/editor.py's own
    # undo_project_file/redo_project_file) — project editing requires a
    # real registered account, never an imported/synthetic identity.
    user_id = ForeignKeyField(User, field='id', backref='edit_history_entries', on_delete='CASCADE')
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    kind = CharField(null=False)
    seq = IntegerField(null=False)
    content = BlobField(null=False)

    class Meta:
        table_name = 'EditHistory'
        indexes = ((('user_id', 'project_name', 'archive_name', 'kind', 'seq'), True),)

class Settings(BaseModel):
    key = CharField(primary_key=True)
    value = CharField()

    class Meta:
        table_name = 'Settings'
