from __future__ import annotations

from automaton.automaton import Automaton


class ProjectCommitMixin:
    async def _activate_project(self, project_id: str, new_automaton: Automaton) -> None:
        async with self.chat_service.acquire_write(project_id):
            pass
