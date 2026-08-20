"""Shared route-registration mechanism for every screen-scoped
*_controller.py (chat/edit_project/label_project/settings — each mapped
to one FE screen, see its own module docstring) — split out of what used
to be one single AvanceController class in controller.py. Each of those
still just decorates its own methods with @get/@post/@put/@delete (see
route below); BaseController.register_routes is the one place that walks
them onto a router, unchanged from before the split.

Route registration order is load-bearing in exactly two places across
the whole app — a literal path segment that must register before a
same-depth {wildcard} route would otherwise swallow it as if it were that
wildcard's own value (FastAPI/Starlette matches routes in registration
order, and inspect.getmembers below walks a class's own methods
alphabetically by name, not by source order):

- get_all_projects_runtime_status ("/api/projects/runtime-status")
  before get_project ("/api/projects/{project_name}") — see settings_
  controller.py's own comment on that pair.
- move_action ("/api/projects/{project_name}/states/{state_name}/
  actions/{action_name}/order") before put_action_field ("...{field}")
  — see edit_project_controller.py's own comment on that pair.

Both pairs live entirely within one controller each, so this class's own
per-controller alphabetical pass is all either one ever needed — nothing
here (or in controller.py's own composition of all four) has to
coordinate registration order *across* controllers at all.
"""
from __future__ import annotations

import inspect

from fastapi import APIRouter

from automaton.automaton import Automaton


def route(method: str, path: str, **kwargs):
    def decorator(func):
        func.__route_info__ = (method, path, kwargs)
        return func
    return decorator


def get(path: str, **kwargs):
    return route("GET", path, **kwargs)


def post(path: str, **kwargs):
    return route("POST", path, **kwargs)


def put(path: str, **kwargs):
    return route("PUT", path, **kwargs)


def delete(path: str, **kwargs):
    return route("DELETE", path, **kwargs)


class BaseController:

    def register_routes(self, router: APIRouter) -> None:
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            info = getattr(member, "__route_info__", None)
            if info is not None:
                method, path, kwargs = info
                router.add_api_route(path, member, methods=[method], **kwargs)

    async def _activate_project(self, new_automaton: Automaton) -> None:
        # Unused param: kept only to match ProjectService's own
        # CommitCallback shape — every project-mutating ProjectService
        # method takes one of these and awaits it once its own write
        # actually lands. Shared here (rather than duplicated on both
        # EditProjectController and SettingsController, the two that
        # actually pass this) since it only ever needs self.chat_service,
        # which every controller subclassing this already has.
        async with self.chat_service.lock:
            self.chat_service.tracking_service.auto_tracking_enabled = True
