# pyright: reportIncompatibleVariableOverride=false
from __future__ import annotations

import hashlib
from datetime import datetime

from peewee import AutoField, BlobField, BooleanField, CharField, CompositeKey, DateTimeField, FloatField, ForeignKeyField, IntegerField, Model, Proxy, TextField


database = Proxy()


class BaseModel(Model):

    class Meta:
        database = database

class Project(BaseModel):
    id = CharField(primary_key=True)
    revision = IntegerField(null=False, default=0)
    published_revision = IntegerField(null=True)
    draft_edit_count = IntegerField(null=False, default=0)
    is_paused = BooleanField(default=False)
    paused_reason = TextField(null=True)
    manually_paused = BooleanField(default=False)
    ui_label = TextField(null=True)
    ui_description = TextField(null=True)
    published_skills = TextField(null=True)

    class Meta:
        table_name = 'Project'

class User(BaseModel):
    id = CharField(primary_key=True)
    provider = CharField(null=True)
    provider_user_id = CharField(null=True)
    email = CharField(null=True)
    name = CharField(null=True)
    picture_url = CharField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    last_login = DateTimeField(null=True)
    active_project = ForeignKeyField(
        Project, field='id', column_name='active_project_id', null=True,
        backref='users_with_active', on_delete='SET NULL',
    )
    active_project_id: str | None
    role = CharField(default='user')
    whatsapp_phone_number = CharField(null=True, unique=True)

    class Meta:
        table_name = 'User'
        indexes = ((('provider', 'provider_user_id'), True),)

SESSION_CLOSE_REASONS = (
    'channel-switch', 'force-new-session', 'manual-user', 'manual-assistant', 'final-state',
    'revision-invalid',
)


class CoreSession(BaseModel):
    id = AutoField()
    username = CharField()
    user = ForeignKeyField(User, field='id', column_name='user_id', null=True, backref='chat_sessions_owned', on_delete='CASCADE')
    user_id: str | None
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='chat_sessions', on_delete='CASCADE')
    project_id: str
    type = CharField(default='live')
    title = CharField(null=True)
    project_revision = IntegerField(null=False)
    datetime_start = DateTimeField(null=True)
    datetime_end = DateTimeField(null=True, index=True)
    start_state = CharField(null=True)
    end_state = CharField(null=True)
    labeled = BooleanField(default=False)
    comment = TextField(null=True)
    labeling_revision = IntegerField(null=False, default=0)
    channel = CharField(null=True, index=True)
    closed_at = DateTimeField(null=True)
    close_reason = CharField(null=True)
    ai_summary = TextField(null=True)

    class Meta:
        table_name = 'CoreSession'
        indexes = ((('username', 'project', 'datetime_start', 'datetime_end'), False), (('username', 'project', 'start_state', 'end_state'), False))

class Message(BaseModel):
    id = AutoField()
    role = CharField()
    content = TextField()
    timestamp = DateTimeField(index=True, default=datetime.utcnow, null=True)
    audio_text = TextField(null=True)
    reaction = TextField(null=True)
    tokens = IntegerField(null=True)
    cache_read_tokens = IntegerField(null=True)
    answered_by = IntegerField(null=True)
    session = ForeignKeyField(CoreSession, null=False, backref='messages', on_delete='CASCADE')
    session_id: int

    class Meta:
        table_name = 'Message'
TRACKING_ORIGINS = ('trigger', 'manual', 'system', 'init-action', 'tool', 'output')


class Tracking(BaseModel):
    id = AutoField()
    session = ForeignKeyField(CoreSession, null=False, backref='tracking', on_delete='CASCADE')
    session_id: int
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    values = TextField(null=True)
    env = TextField(null=True)
    action_env = TextField(null=True)
    local_memory = TextField(null=True)
    output = TextField(null=True)
    tool_calls = TextField(null=True)
    expected_state = CharField(null=True)
    expected_values = TextField(null=True)
    comment = TextField(null=True)
    old_state = CharField(null=True, index=True)
    action = CharField(null=True)
    new_state = CharField(null=True, index=True)
    message = ForeignKeyField(Message, null=True, backref='tracking_row', on_delete='SET NULL')
    message_id: int | None
    origin = CharField(null=True)

    class Meta:
        table_name = 'Tracking'

class File(BaseModel):
    hash = CharField(primary_key=True)
    content = BlobField(null=False)
    content_type = CharField(null=False)
    size = IntegerField(null=False)

    class Meta:
        table_name = 'File'

    @staticmethod
    def hash_of(content: bytes, content_type: str) -> str:
        digest = hashlib.sha256()
        digest.update(content_type.encode('utf-8'))
        digest.update(b'\x00')
        digest.update(content)
        return digest.hexdigest()

    @classmethod
    def put(cls, content: bytes, content_type: str) -> str:
        key = cls.hash_of(content, content_type)
        cls.insert(
            hash=key, content=content, content_type=content_type, size=len(content),
        ).on_conflict_ignore().execute()
        return key


class Archive(BaseModel):
    id = AutoField()
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='archives', on_delete='CASCADE')
    project_id: str
    archive_name = CharField(index=True, null=False)
    revision = IntegerField(null=False, default=0)
    hash = ForeignKeyField(File, field='hash', column_name='hash', backref='archives', null=False, on_delete='RESTRICT')
    hash_id: str

    @property
    def content(self) -> bytes:
        return self.hash.content

    @property
    def content_type(self) -> str:
        return self.hash.content_type

    class Meta:
        table_name = 'Archive'
        indexes = ((('project', 'archive_name', 'revision'), True),)

class Drive(BaseModel):
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='drive_files', on_delete='CASCADE')
    project_id: str
    user = ForeignKeyField(User, field='id', column_name='user_id', backref='drive_files', on_delete='CASCADE')
    user_id: str
    session = ForeignKeyField(CoreSession, null=True, backref='drive_files', on_delete='SET NULL')
    session_id: int | None
    path = CharField(null=False)
    hash = ForeignKeyField(File, field='hash', column_name='hash', backref='drive_files', null=False, on_delete='RESTRICT')
    hash_id: str
    updated_at = DateTimeField(default=datetime.utcnow)
    downloads = IntegerField(null=False, default=0)

    @property
    def content(self) -> bytes:
        return self.hash.content

    @property
    def content_type(self) -> str:
        return self.hash.content_type

    @property
    def size(self) -> int:
        return self.hash.size

    class Meta:
        table_name = 'Drive'
        indexes = ((('project', 'user', 'path'), True),)

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
    user = ForeignKeyField(User, field='id', column_name='user_id', null=True, backref='tests_owned', on_delete='CASCADE')
    user_id: str | None
    project_id = CharField(index=True)
    session = ForeignKeyField(CoreSession, null=True, backref='tests', on_delete='CASCADE')
    session_id: int | None
    strategy = CharField()
    project_draft_edit_count = IntegerField(null=False)
    session_labeling_revision = IntegerField(null=True)
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
    run_id: int
    session = ForeignKeyField(CoreSession, null=False, backref='test_observations', on_delete='CASCADE')
    session_id: int
    message = ForeignKeyField(Message, null=True, backref='test_observations', on_delete='SET NULL')
    message_id: int | None
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
    """Something that resolved to None at runtime instead of raising —
    a cross-project reference nobody could answer, a project that stopped
    building — kept for the person to read."""
    id = AutoField()
    user_id = ForeignKeyField(User, field='id', backref='system_warnings', on_delete='CASCADE')
    project_id = CharField(index=True)
    kind = CharField()
    message = TextField()
    file = CharField(null=True)
    line = IntegerField(null=True)
    timestamp = DateTimeField(index=True, default=datetime.utcnow)

    class Meta:
        table_name = 'SystemWarning'

class AiUsage(BaseModel):
    """One row per successful ai-service generate call (see AiService.
    generate_stream_with_metadata's on_metadata tap) — raw, un-aggregated,
    the same way Tracking rows are: day/provider totals for Manage
    services' own consumption bar and trend chart are grouped from these
    at read time (db/ai_usage.py), not maintained as a running counter.
    `duration` is the call's wall-clock seconds, from the request to the
    provider's usage report at the end of its stream (or to the error
    that ended it). `time_to_first_chunk` is the wall-clock seconds to
    the first byte the provider sent back, null when none ever arrived
    (a tool-call round the model answered with no preceding text, or a
    call that failed before yielding anything — see
    AiService._UsageTap.first_chunk_received). `outcome` is "success"
    for a completed call, else one of AiService._outcome_for's
    categories — a failed call still gets a row, with 0 tokens and
    whatever duration elapsed before the error (see
    AiService._UsageTap.record_failure)."""
    id = AutoField()
    provider_label = CharField(index=True)
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    input_tokens = IntegerField(default=0)
    output_tokens = IntegerField(default=0)
    cache_read_tokens = IntegerField(default=0)
    cache_creation_tokens = IntegerField(default=0)
    duration = FloatField(default=0.0)
    time_to_first_chunk = FloatField(null=True)
    outcome = CharField(index=True, default='success')

    class Meta:
        table_name = 'AiUsage'

class DbUsage(BaseModel):
    """One row per call into a db-layer mixin's public method (see
    db/instrumentation.py's instrument_queries, applied to every mixin
    class Db is built from) — raw, un-aggregated, the same way AiUsage
    is. `query_name` is the method that ran (e.g. 'get_chat_session'),
    `timestamp` when it was called (call start, not completion),
    `duration` its wall-clock seconds, `outcome` "success" or "failure"
    (a raised exception still gets a row, with whatever duration elapsed
    before it propagated), and `kind` "read" or "write" — a method is
    "write" only if instrumentation.py's own @write decorator marks it,
    never guessed from its body; everything undecorated defaults to
    "read"."""
    id = AutoField()
    query_name = CharField(index=True)
    timestamp = DateTimeField(index=True, default=datetime.utcnow)
    duration = FloatField(default=0.0)
    outcome = CharField(index=True, default='success')
    kind = CharField(index=True, default='read')

    class Meta:
        table_name = 'DbUsage'

class Translation(BaseModel):
    """One label already translated, keyed by its own context (`key`) and
    the exact source/destination locale pair — see docs/BUS.md's
    `turn.translation`, the only writer. Global, not per-session: the same
    label in the same language pair reuses the same row for every
    session/project that asks."""
    id = AutoField()
    key = CharField()
    src_lang = CharField()
    src_text = TextField()
    dst_lang = CharField()
    dst_text = TextField()
    timestamp = DateTimeField(default=datetime.utcnow)

    class Meta:
        table_name = 'Translation'
        indexes = (
            (('key', 'src_lang', 'src_text', 'dst_lang'), True),
            (('key', 'src_lang', 'src_text'), False),
        )

class EditHistory(BaseModel):
    """Per-(user, project, file) undo/redo trail for the project editor —
    named EditHistory (not just History) to read unambiguously as project-
    file edit history, not e.g. chat/session history."""
    id = AutoField()
    user_id = ForeignKeyField(User, field='id', backref='edit_history_entries', on_delete='CASCADE')
    project_id = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    kind = CharField(null=False)
    seq = IntegerField(null=False)
    content = BlobField(null=True)
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
    project_id: str
    max_shares = IntegerField()
    created_by = ForeignKeyField(User, field='id', column_name='created_by_id', null=True, backref='invites_created', on_delete='SET NULL')
    created_by_id: str | None

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
    user_id: str
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='user_projects', on_delete='CASCADE')
    project_id: str
    accepted_terms = ForeignKeyField(Archive, column_name='accepted_terms_id', backref='accepted_by', null=True, on_delete='SET NULL')
    accepted_terms_id: int | None
    invite = ForeignKeyField(Invite, column_name='invite_id', null=True, backref='redemptions', on_delete='SET NULL')
    invite_id: int | None
    invite_timestamp = DateTimeField(null=True)
    ai_summary = TextField(null=True)

    class Meta:
        table_name = 'UserProject'
        primary_key = CompositeKey('user', 'project')

class TrialSession(BaseModel):
    """One row per test session a user has started from the app store
    (AppDetailPanel's "Try me!"), used to enforce PlatformService.
    TRIAL_SESSIONS_PER_APP. It is deliberately NOT a counter column on
    UserProject: that row is created by terms acceptance or invite
    redemption and deleted by uninstall_project, and its mere existence
    is what user_has_project_access grants on — a trial must neither
    reset on uninstall nor confer project access. `revision` records the
    project's published_revision at the time, so a future policy can
    scope the quota to a revision without a schema change; the quota
    enforced today counts every row for the (user, project) pair."""
    id = AutoField()
    user = ForeignKeyField(User, field='id', column_name='user_id', backref='trial_sessions', on_delete='CASCADE')
    user_id: str
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='trial_sessions', on_delete='CASCADE')
    project_id: str
    revision = IntegerField(null=True)
    started_at = DateTimeField(default=datetime.utcnow)

    class Meta:
        table_name = 'TrialSession'

class Settings(BaseModel):
    key = CharField(primary_key=True)
    value = CharField()

    class Meta:
        table_name = 'Settings'

class AppRating(BaseModel):
    """How the user rated one closed session's revision of the app —
    thumb up/down, stored as 5/1 (see AppRatingRequest). One vote per
    (user, project, revision): a later vote on the same revision
    overwrites the earlier one rather than adding a row. `session` records
    which session the vote was asked from; it is informational only and
    plays no part in the (user, project, revision) dedup, so a later vote
    from a different session on the same revision still overwrites."""
    id = AutoField()
    user = ForeignKeyField(User, field='id', column_name='user_id', backref='app_ratings', on_delete='CASCADE')
    user_id: str
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='app_ratings', on_delete='CASCADE')
    project_id: str
    session = ForeignKeyField(CoreSession, null=True, backref='app_ratings', on_delete='SET NULL')
    session_id: int | None
    revision = IntegerField(null=False)
    rating = IntegerField(null=False)
    timestamp = DateTimeField(default=datetime.utcnow)

    class Meta:
        table_name = 'AppRating'
        indexes = ((('user', 'project', 'revision'), True),)

class Task(BaseModel):
    """A hibernated scheduled task (see scheduler/task.py and
    scheduler/persisted_scheduler.py) — this table *is* the persisted
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
    user_id: str
    project = ForeignKeyField(Project, field='id', column_name='project_id', backref='tasks', on_delete='CASCADE')
    project_id: str
    run_at = DateTimeField(index=True)
    payload = TextField()
    ui_label = TextField()
    ui_description = TextField()
    status = CharField(default='pending', index=True)
    error = TextField(null=True)
    created_at = DateTimeField(default=datetime.utcnow)
    dispatched_at = DateTimeField(null=True)
    settled_at = DateTimeField(null=True)

    class Meta:
        table_name = 'Task'
