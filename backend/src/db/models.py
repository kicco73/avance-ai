from __future__ import annotations

from datetime import datetime

from peewee import AutoField, BlobField, BooleanField, CharField, CompositeKey, DateTimeField, ForeignKeyField, IntegerField, Model, Proxy, TextField

from chat.channels import NATIVE_CHAT

database = Proxy()


class BaseModel(Model):

    class Meta:
        database = database

class Project(BaseModel):
    # The project's own declared `project.id` — mandatory, globally unique,
    # a plain identifier (letters/digits/underscore). What *other* projects
    # in the same declared `project.family` (see automaton_builder.py,
    # never stored here — always read fresh off each project's own
    # index.yml) reach this one as through automaton.*, and the sole
    # identity every other table keys on.
    id = CharField(primary_key=True)
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
    ui_label = TextField(null=True)
    ui_description = TextField(null=True)

    class Meta:
        table_name = 'Project'

class User(BaseModel):
    id = CharField(primary_key=True)
    # "google"/"whatsapp" — which AuthProvider (or channel) verified this
    # account. Nullable along with provider_user_id/name: UserMixin's
    # get_active_project_id/set_active_project_id/clear_active_project_id
    # still take a bare `user: str` (resolved against `id`, see db/users.py)
    # rather than a real FK — set_active_project_id's own create-fallback,
    # for a user with no User row yet, has no provider identity to fill
    # these with.
    provider = CharField(null=True)
    # The provider's own opaque id for this account (Google: the "sub"
    # claim) — stable identity, unlike email, which a provider account
    # could in principle change. Unused for provider="whatsapp".
    provider_user_id = CharField(null=True)
    # Nullable: a WhatsApp-native registration (see AuthService.
    # register_via_whatsapp) has no email at all — id is the phone number
    # for those rows instead.
    email = CharField(null=True)
    name = CharField(null=True)
    picture_url = CharField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    last_login = DateTimeField(null=True)
    # Absorbs the old standalone Settings table — its only field, the
    # user's own active project. Nullable: a user with no projects yet
    # still needs a row. on_delete='SET NULL' so deleting the active
    # project never leaves a dangling reference — every user pointing at
    # it just goes back to "no active project" (get_active_project_id
    # picks a fallback the next time it's read).
    active_project = ForeignKeyField(
        Project, field='id', column_name='active_project_id', null=True,
        backref='users_with_active', on_delete='SET NULL',
    )
    role = CharField(default='user')
    whatsapp_phone_number = CharField(null=True, unique=True)

    class Meta:
        table_name = 'User'
        indexes = ((('provider', 'provider_user_id'), True),)

SESSION_CLOSE_REASONS = ('channel-switch', 'force-new-session', 'manual-user', 'manual-assistant')


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
    # Bare field name (not "project_id") so peewee derives the raw-scalar
    # instance accessor as `.project_id` — same convention as `user`/`user_id`
    # just above.
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='chat_sessions', on_delete='CASCADE')
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
    datetime_end = DateTimeField(null=True, index=True)
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
    # Which channel this session was opened through — fixed at creation,
    # never touched again (see chat/session_manager.py's create_session).
    # 'native-chat' (the web SPA) is the default so migration-strategy:
    # upgrade backfills every pre-existing row with it automatically.
    channel = CharField(default=NATIVE_CHAT, index=True)
    closed_at = DateTimeField(null=True)
    close_reason = CharField(null=True)
    ai_summary = TextField(null=True)

    class Meta:
        table_name = 'ChatSession'
        indexes = ((('username', 'project', 'datetime_start', 'datetime_end'), False), (('username', 'project', 'start_state', 'end_state'), False))

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
    tokens = IntegerField(null=True)
    session = ForeignKeyField(ChatSession, null=False, backref='messages', on_delete='CASCADE')

    class Meta:
        table_name = 'Message'

TRACKING_ORIGINS = ('trigger', 'manual', 'system', 'init-action')


class Tracking(BaseModel):
    id = AutoField()
    session = ForeignKeyField(ChatSession, null=False, backref='tracking', on_delete='CASCADE')
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)
    env = TextField(null=True)
    action_env = TextField(null=True)
    # JSON list of {name, arguments, result} — one entry per tool call
    # the model made this turn (see ai.ai_service.AiService's own
    # tool-call loop), in the order they ran. Written as its own row,
    # same shape as env/action_env above, never merged into `values`.
    tool_calls = TextField(null=True)
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
    origin = CharField(null=True)

    class Meta:
        table_name = 'Tracking'

class Archive(BaseModel):
    id = AutoField()
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='archives', on_delete='CASCADE')
    archive_name = CharField(index=True, null=False)
    revision = IntegerField(null=False, default=0)
    content = BlobField(null=False)
    content_type = CharField(null=False)

    class Meta:
        table_name = 'Archive'
        # One row per revision — a published revision's own rows are never
        # updated in place again (see Db.save_project_files's fork step),
        # so (project, archive_name) alone can no longer be unique.
        indexes = ((('project', 'archive_name', 'revision'), True),)

class StateRemap(BaseModel):
    """An administrative fact about a published revision, not a
    conversation event (never goes in Tracking). Flattened on every
    write so resolving a key is always a single lookup, never a chain."""
    project_id = CharField()
    old_key = CharField()
    new_key = CharField()

    class Meta:
        table_name = 'StateRemap'
        primary_key = CompositeKey('project_id', 'old_key')

class Test(BaseModel):
    id = AutoField()
    username = CharField(null=True)
    # Nullable for the same reason as ChatSession.user above — this run's
    # own username may be a real registered account or an imported
    # transcript's synthetic identity. Needed as its own FK (not just
    # reachable via session below) because session is itself null for a
    # project-wide aggregate run — see the comment on it just below.
    user = ForeignKeyField(User, field='id', column_name='user_id', null=True, backref='tests_owned', on_delete='CASCADE')
    project_id = CharField(index=True)
    # None means "every labeled session of the project", never a single
    # unresolved session — same dual as BenchmarkCalculator(session_id=
    # None|int) (see metrics/metrics_framework/benchmark_metrics/calculator.py).
    session = ForeignKeyField(ChatSession, null=True, backref='tests', on_delete='CASCADE')
    strategy = CharField()
    # The project's own draft edit count at the moment this run was
    # created — captured once, up front, regardless of which revision is
    # published (see ChatSession.project_revision, same idea).
    project_draft_edit_count = IntegerField(null=False)
    session_labeling_revision = IntegerField(null=True)
    # Only ever set for strategy='batch'/'batch_lite' — stays null for 'turn_by_turn'.
    batch_segments = IntegerField(null=True)
    ai_model_snapshot = TextField(null=True)
    results = TextField(null=True)

    class Meta:
        table_name = 'Test'
        indexes = (
            (('username', 'project_id'), False),
            (('session', 'strategy', 'project_draft_edit_count', 'session_labeling_revision'), True),
        )

class TestObservation(BaseModel):
    """A replay's own signal snapshot/transition — the same shape
    Tracking carries for production, but on its own table so a replay
    can never be mistaken for (or overwrite) real conversation data."""
    id = AutoField()
    run = ForeignKeyField(Test, null=False, backref='observations', on_delete='CASCADE')
    session = ForeignKeyField(ChatSession, null=False, backref='test_observations', on_delete='CASCADE')
    message = ForeignKeyField(Message, null=True, backref='test_observations', on_delete='SET NULL')
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)

    class Meta:
        table_name = 'TestObservation'
        indexes = ((('run', 'session'), False),)

class TestAggregateResult(BaseModel):
    id = AutoField()
    project_id = CharField(index=True)
    revision = IntegerField(null=False)
    project_draft_edit_count = IntegerField(null=False)
    kind = CharField()
    target = CharField(default='')
    strategy = CharField()
    results = TextField(null=False)
    created_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        table_name = 'TestAggregateResult'
        indexes = (
            (('project_id', 'revision'), False),
            (('project_id', 'revision', 'project_draft_edit_count', 'kind', 'target', 'strategy'), True),
        )

class SystemWarning(BaseModel):
    """A cross-project reference (automaton.<project>.state/env.<key>)
    that resolved to None at runtime instead of raising — one of three
    failure kinds ('project_not_found', 'no_session', 'env_key_not_declared')."""
    id = AutoField()
    # Not nullable: unlike ChatSession/Test's own username, this
    # is always Session().user (see tracking/automaton_namespace.py's
    # AutomatonNamespace) — a real registered account is the only thing
    # ever authenticated enough to reach this code path at all.
    user_id = ForeignKeyField(User, field='id', backref='system_warnings', on_delete='CASCADE')
    project_id = CharField(index=True)
    kind = CharField()
    message = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow)

    class Meta:
        table_name = 'SystemWarning'

class AiTokenUsage(BaseModel):
    """One row per successful ai-service generate call (see AiService.
    generate_stream_with_metadata's on_metadata tap) — raw, un-aggregated,
    the same way Tracking rows are: day/provider totals for Manage
    services' own consumption bar and trend chart are grouped from these
    at read time (db/ai_usage.py), not maintained as a running counter."""
    id = AutoField()
    # "<driver>/<model>", matching AiService._build_labeled_providers'
    # own label — shared across the live and test cascades, since both
    # bill against the same real-world provider/model either way.
    provider_label = CharField(index=True)
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    input_tokens = IntegerField(default=0)
    output_tokens = IntegerField(default=0)

    class Meta:
        table_name = 'AiTokenUsage'

class ProjectObserverIndex(BaseModel):
    """Reverse index of automaton.* cross-project references, rebuilt
    from scratch for `observer_project_id` on every index.yml build.
    Queried both as "who observes me" and "who do I depend on". Keyed by
    project_id (the stable token an automaton.<id> reference names) on
    both sides now that project identity is unified."""
    id = AutoField()
    project_id = CharField(index=True)
    observer_project_id = CharField(index=True)

    class Meta:
        table_name = 'ProjectObserverIndex'
        indexes = ((('project_id', 'observer_project_id'), True),)

class EditHistory(BaseModel):
    """Per-(user, project, file) undo/redo trail for the project editor —
    named EditHistory (not just History) to read unambiguously as project-
    file edit history, not e.g. chat/session history."""
    id = AutoField()
    # Not nullable: always Session().user (see project/editor.py's own
    # undo_project_file/redo_project_file) — project editing requires a
    # real registered account, never an imported/synthetic identity.
    user_id = ForeignKeyField(User, field='id', backref='edit_history_entries', on_delete='CASCADE')
    project_id = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    kind = CharField(null=False)
    seq = IntegerField(null=False)
    # Null for a rename-marker row (see rename_target below); set for an
    # ordinary content-snapshot row.
    content = BlobField(null=True)
    # Set only on a rename-marker row — the *other* name involved in this
    # rename step (see HistoryMixin.rename_project_file/undo_project_file/
    # redo_project_file in db/history.py): for an 'undo' row, the name to
    # rename back to; for a 'redo' row, the name to rename forward to.
    # Null for an ordinary content-snapshot row.
    rename_target = CharField(null=True)

    class Meta:
        table_name = 'EditHistory'
        indexes = ((('user_id', 'project_id', 'archive_name', 'kind', 'seq'), True),)

class Invite(BaseModel):
    """A "share project" link's own row (see ShareProjectDialog.vue/
    shareLink.js) — one per dialog-open, never reused. `code` is the
    short random token the link/QR actually carries; project_id/
    expires_at/max_shares are the invite-only-registration budget
    InviteManager enforces (project/invites.py) before a brand-new
    identity is ever allowed to self-register through it (see
    AuthService.complete_registration). How many people actually have,
    see UserProject.invite's own backref (`redemptions`) below — never
    stored on this row itself, always counted live."""
    id = AutoField()
    code = CharField(unique=True, index=True)
    created_at = DateTimeField(default=datetime.utcnow)
    expires_at = DateTimeField()
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='invites', on_delete='CASCADE')
    max_shares = IntegerField()
    # Nullable + SET NULL, not CASCADE: unlike EditHistory/SystemWarning's
    # own user_id (whose owner's own data it *is*), an Invite is a link
    # other people are actively using — deleting the admin who generated
    # it must never break it for them.
    created_by = ForeignKeyField(User, field='id', column_name='created_by_id', null=True, backref='invites_created', on_delete='SET NULL')

    class Meta:
        table_name = 'Invite'

class UserProject(BaseModel):
    """One row per (user, project) once that user has ever accepted the
    project's legal/terms.md, OR registered onto the platform through an
    invite to this project (see ProjectService.redeem_invite) — either
    half may be set independently of the other, and neither implies the
    other. accepted_terms points at the specific Archive row
    (archive_name=legal/terms.md) they accepted; a mismatch against the
    project's current such row means the terms changed since and must be
    re-accepted before a new live session can open (see
    ProjectService.legal_terms_pending/accept_legal_terms). invite/
    invite_timestamp record which Invite (if any) brought this user to
    this project, and when — Invite.redemptions (this FK's own backref)
    is how InviteManager counts a code's max_shares usage, by counting
    rows here rather than any counter stored on Invite itself."""
    user = ForeignKeyField(User, field='id', column_name='user_id', backref='user_projects', on_delete='CASCADE')
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='user_projects', on_delete='CASCADE')
    accepted_terms = ForeignKeyField(Archive, column_name='accepted_terms_id', backref='accepted_by', null=True, on_delete='SET NULL')
    invite = ForeignKeyField(Invite, column_name='invite_id', null=True, backref='redemptions', on_delete='SET NULL')
    invite_timestamp = DateTimeField(null=True)
    ai_summary = TextField(null=True)

    class Meta:
        table_name = 'UserProject'
        primary_key = CompositeKey('user', 'project')

class Settings(BaseModel):
    key = CharField(primary_key=True)
    value = CharField()

    class Meta:
        table_name = 'Settings'

class Task(BaseModel):
    """A hibernated scheduled task (see jobs/task.py and
    job/persisted_scheduler.py) — this table *is* the persisted
    scheduler's queue: one row per task still owed to the future, plus
    a terminal row once it settled (kept as an audit trail, never run
    again). `type` selects the hydrator that turns `payload` (JSON, the
    task's own dehydrate()) back into a live Task; `user` and `project`
    are real foreign keys cascading on delete, so erasing a user or
    deleting a project takes their pending tasks with it. `ui_label`/
    `ui_description` are what a listing shows, stored at submit time so
    the UI never hydrates a row. `run_at` is naive UTC, like every
    other DateTimeField here."""
    id = AutoField()
    key = CharField(unique=True)
    type = CharField(index=True)
    user = ForeignKeyField(User, field='id', column_name='user_id', backref='tasks', on_delete='CASCADE')
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='tasks', on_delete='CASCADE')
    run_at = DateTimeField(index=True)
    payload = TextField()
    ui_label = TextField()
    ui_description = TextField()
    # pending -> dispatched -> done | failed; pending -> canceled.
    status = CharField(default='pending', index=True)
    error = TextField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    # When the row was claimed (see TaskMixin.claim_due_task) — a claim
    # older than the scheduler's lease with no settlement is a dead
    # process's, and goes back to pending (requeue_stale_dispatched_tasks).
    dispatched_at = DateTimeField(null=True)
    settled_at = DateTimeField(null=True)

    class Meta:
        table_name = 'Task'
