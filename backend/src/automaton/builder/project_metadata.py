from __future__ import annotations

from dataclasses import dataclass

from ruamel.yaml import YAML

from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


def load_yaml(text: str):
    # A fresh YAML per call: a shared instance is not thread-safe across concurrent load() calls.
    return YAML(typ='rt').load(text)


@dataclass(frozen=True)
class ProjectMetadata:
    project_id: str
    family: str | None
    revision: int
    ui_label: str | None
    ui_description: str | None
    autotracking_on_ai_message: bool
    talk_enabled: bool

    @classmethod
    def from_raw(cls, raw: dict, *, legacy_project_id: str | None = None) -> "ProjectMetadata":
        raw_project = raw.get("project")
        if raw_project is None and legacy_project_id is not None:
            raw_project = {"id": legacy_project_id}
        elif isinstance(raw_project, dict) and "id" not in raw_project and legacy_project_id is not None:
            raw_project = {**raw_project, "id": legacy_project_id}
        if not isinstance(raw_project, dict):
            raise ValueError(
                "'project' is required and must be a mapping of fields (id, family, ui-label, "
                "ui-description, signal-tracking-on-ai-message, talk-enabled), got "
                f"{type(raw_project).__name__ if raw_project is not None else 'nothing'}."
            )
        project_id = raw_project.get("id")
        if not isinstance(project_id, str) or not project_id.isidentifier():
            raise ValueError(
                f"project.id {project_id!r} is required and must be a valid identifier — letters, "
                "digits, and underscores only, and it can't start with a digit."
            )
        family = raw_project.get("family") or None
        if family is not None and not isinstance(family, str):
            raise ValueError(f"project.family {family!r} must be a string.")
        revision = raw_project.get("revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError(f"project.revision {revision!r} must be a non-negative integer.")
        return cls(
            project_id=project_id,
            family=family,
            revision=revision,
            ui_label=raw_project.get("ui-label"),
            ui_description=raw_project.get("ui-description"),
            autotracking_on_ai_message=raw_project.get("signal-tracking-on-ai-message", False),
            talk_enabled=raw_project.get("talk-enabled", True),
        )


def read_declared_env_keys(index_yml_text: str) -> tuple[str | None, str | None, frozenset[str]]:
    try:
        raw = load_yaml(index_yml_text)
    except Exception as exc:
        logger.warning("Failed to parse index.yml for known_projects_env_keys: %s", exc)
        return None, None, frozenset()
    if not isinstance(raw, dict):
        return None, None, frozenset()
    raw_project = raw.get("project")
    project_id = raw_project.get("id") if isinstance(raw_project, dict) else None
    if not isinstance(project_id, str) or not project_id.isidentifier():
        project_id = None
    family = raw_project.get("family") if isinstance(raw_project, dict) else None
    if not isinstance(family, str) or not family:
        family = None
    raw_env = raw.get("env")
    env_keys = frozenset(raw_env.keys()) if isinstance(raw_env, dict) else frozenset()
    return project_id, family, env_keys


def peek_declared_revision(index_yml_text: str) -> int | None:
    try:
        raw = load_yaml(index_yml_text)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    raw_project = raw.get("project")
    if not isinstance(raw_project, dict):
        return None
    revision = raw_project.get("revision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None
