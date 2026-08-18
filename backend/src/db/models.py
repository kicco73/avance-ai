from __future__ import annotations

from datetime import datetime

from peewee import AutoField, CharField, DateTimeField, ForeignKeyField, IntegerField, Model, Proxy, TextField

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
    datetime_start = DateTimeField(null=True)
    datetime_end = DateTimeField(null=True)
    start_state = CharField(null=True)
    end_state = CharField(null=True)

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
    content = TextField(null=False)

    class Meta:
        indexes = ((('project_name', 'archive_name'), True),)

class History(BaseModel):
    id = AutoField()
    user_id = CharField(index=True, null=False)
    project_name = CharField(index=True, null=False)
    archive_name = CharField(index=True, null=False)
    kind = CharField(null=False)
    seq = IntegerField(null=False)
    content = TextField(null=False)

    class Meta:
        indexes = ((('user_id', 'project_name', 'archive_name', 'kind', 'seq'), True),)

DEFAULT_USER = "user"
