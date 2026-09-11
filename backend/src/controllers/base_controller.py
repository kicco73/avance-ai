"""Shared route-registration mechanism for every screen-scoped
*_controller.py.

Routes are registered in path order, not in the order inspect.getmembers
happens to walk the methods, and a {wildcard} segment sorts after every
literal at its own depth. So a controller that declares both
/projects/file-types and /projects/{project_id} gets them in the only
order FastAPI can dispatch correctly, whatever the two methods are
called. It used to depend on the method names sorting the right way,
which is a property nobody could see at the call site."""
from __future__ import annotations

import inspect

from fastapi import APIRouter

WILDCARD = "￿"


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
        for method, path, kwargs, member in self._declared_routes():
            router.add_api_route(path, member, methods=[method], **kwargs)

    def _declared_routes(self) -> list[tuple]:
        declared = [
            (*member.__route_info__, member)
            for _, member in inspect.getmembers(self, predicate=inspect.ismethod)
            if getattr(member, "__route_info__", None) is not None
        ]
        return sorted(declared, key=lambda route: self._dispatch_order(route[1]))

    def _dispatch_order(self, path: str) -> tuple:
        return tuple(
            WILDCARD if segment.startswith("{") else segment
            for segment in path.split("/")
        )
