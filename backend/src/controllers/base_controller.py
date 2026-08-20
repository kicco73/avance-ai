"""Shared route-registration mechanism for every screen-scoped
*_controller.py. Registration order matters in one place: a literal
path segment must register before a same-depth {wildcard} route, since
inspect.getmembers walks methods alphabetically, not by source order
(see edit_project_controller.py's move_action/put_action_field)."""
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
        # CommitCallback shape. The lock itself is the whole point — it
        # serializes this commit against a concurrent chat turn.
        async with self.chat_service.lock:
            pass
