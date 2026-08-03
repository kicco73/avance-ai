from __future__ import annotations

from .models import Archive, ChatSession, History, Message, Tracking


class ProjectMixin:

    def reset_project(self, project_name: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where(ChatSession.project_name == project_name)
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where(ChatSession.project_name == project_name).execute()

    def reset_project_for_user(self, username: str, project_name: str) -> None:
        session_ids = ChatSession.select(ChatSession.id).where((ChatSession.username == username) & (ChatSession.project_name == project_name))
        Tracking.delete().where(Tracking.session.in_(session_ids)).execute()
        Message.delete().where(Message.session.in_(session_ids)).execute()
        ChatSession.delete().where((ChatSession.username == username) & (ChatSession.project_name == project_name)).execute()

    def reset_all(self) -> None:
        Tracking.delete().execute()
        Message.delete().execute()
        ChatSession.delete().execute()

    def get_archive(self, project_name: str, archive_name: str) -> str | None:
        row = Archive.get_or_none((Archive.project_name == project_name) & (Archive.archive_name == archive_name))
        return row.content if row is not None else None

    def get_archives(self, project_name: str) -> dict:
        return {row.archive_name: row.content for row in Archive.select(Archive.archive_name, Archive.content).where(Archive.project_name == project_name)}

    def save_project_files(self, project_name: str, files: dict[str, str]) -> None:
        for archive_name, content in files.items():
            existing = Archive.get_or_none((Archive.project_name == project_name) & (Archive.archive_name == archive_name))
            if existing is None:
                Archive.create(project_name=project_name, archive_name=archive_name, revision=0, content=content)
            else:
                Archive.update(content=content, revision=Archive.revision + 1).where(Archive.id == existing.id).execute()

    def list_projects(self) -> list[str]:
        return [p.project_name for p in Archive.select(Archive.project_name).distinct()]

    def list_archives(self, project_name: str) -> list[str]:
        return [p.archive_name for p in Archive.select(Archive.archive_name).where(Archive.project_name == project_name)]

    def delete_archive(self, project_name: str, archive_name: str) -> None:
        Archive.delete().where((Archive.project_name == project_name) & (Archive.archive_name == archive_name)).execute()
        History.delete().where((History.project_name == project_name) & (History.archive_name == archive_name)).execute()

    def delete_archives(self, project_name: str) -> None:
        Archive.delete().where(Archive.project_name == project_name).execute()
        History.delete().where(History.project_name == project_name).execute()
