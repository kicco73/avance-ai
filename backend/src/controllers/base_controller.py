"""Shared route-registration mechanism for every screen-scoped
*_controller.py. Registration order matters in one place: a literal
path segment must register before a same-depth {wildcard} route, since
inspect.getmembers walks methods alphabetically, not by source order
(see edit_project_controller.py's move_action/put_action_field)."""
from __future__ import annotations

import inspect

from fastapi import APIRouter


def route(method: str, path: str, role: str | None = "user", **kwargs):
    def decorator(func):
        func.__route_info__ = (method, path, kwargs)
        func.__required_role__ = role
        return func
    return decorator


def get(path: str, role: str | None = "user", **kwargs):
    return route("GET", path, role=role, **kwargs)


def post(path: str, role: str | None = "user", **kwargs):
    return route("POST", path, role=role, **kwargs)


def put(path: str, role: str | None = "user", **kwargs):
    return route("PUT", path, role=role, **kwargs)


def delete(path: str, role: str | None = "user", **kwargs):
    return route("DELETE", path, role=role, **kwargs)


class BaseController:

    def register_routes(self, router: APIRouter) -> None:
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            info = getattr(member, "__route_info__", None)
            if info is not None:
                method, path, kwargs = info
                router.add_api_route(path, member, methods=[method], **kwargs)
