"""Single point of model-lifecycle management: which automaton is active,
listing available models, and validating/staging/committing activations,
uploads, and deletions.

Same principle as db.py's isolation of persistence: one module, one
responsibility. main.py routes HTTP/WS traffic into these domain functions —
it doesn't contain this logic itself.

Ownership boundary: this module owns *which automaton definition is active*
and the on-disk `models/` directory. It deliberately does NOT own the chat
session (history, current_state, auto-tracking) or the lock serializing chat
turns — those are main.py's concern and staying there avoids a circular
import (main.py needs list_models()/activate_model() from here; this module
would need main.py's `session`/`chat_lock` to reset them). Instead,
`activate_model()` and `put_model()` accept a `commit` callback, supplied by
main.py, invoked exactly once on success with the newly active Automaton —
main.py's callback is what actually resets the session, holding its own
chat_lock while it does, so a switch/create-or-replace can never race an
in-flight chat turn. Both call sites pass the *same* callback, so that reset
logic itself is never duplicated between them.
"""
from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Awaitable, Callable

from automaton import Automaton, load_automaton

MODELS_DIR = Path(__file__).parent / "models"
DEFAULT_MODEL_NAME = "default"

# Called with the newly-active Automaton once activate_model()/put_model()
# have committed it — see module docstring.
CommitCallback = Callable[[Automaton], Awaitable[None]]

_active_automaton: Automaton | None = None
_active_model_name: str | None = None


def _set_active(model_name: str, automaton: Automaton) -> None:
    """The one place both pieces of "which model is active" state
    (the automaton itself and its name) are updated together — every
    call site that swaps the active automaton (boot, switch, put) goes
    through this, so the two can never drift apart."""
    global _active_automaton, _active_model_name
    _active_automaton = automaton
    _active_model_name = model_name


def init_default_model() -> Automaton:
    """Call once at startup: loads models/default/index.yml as the initial
    active automaton, regardless of what was active via a switch/upload in
    a previous run — there's no persistence of "which model was active"."""
    automaton = load_automaton(MODELS_DIR / DEFAULT_MODEL_NAME / "index.yml")
    _set_active(DEFAULT_MODEL_NAME, automaton)
    return automaton


def get_active_automaton() -> Automaton:
    return _active_automaton


def get_active_model_name() -> str | None:
    return _active_model_name


def list_models() -> dict:
    """For GET /api/models: every subdirectory of models/ containing an
    index.yml — the content isn't validated here, only its presence; real
    validation happens at activate/put time. Directories starting with '.'
    are excluded: those are staging artifacts (e.g. a `.tmp_<uuid>` left by
    an interrupted put), never models. `active` is reported once at the
    root, separately from the name list, rather than per-entry."""
    if not MODELS_DIR.is_dir():
        names = []
    else:
        names = sorted(
            entry.name
            for entry in MODELS_DIR.iterdir()
            if entry.is_dir() and not entry.name.startswith(".") and (entry / "index.yml").is_file()
        )
    return {"models": names, "active": _active_model_name}


def _is_safe_model_name(model_name: str) -> bool:
    """No path traversal: must be a single plain path segment — not empty,
    not '.'/'..', no separators, resolving to itself when treated as a bare
    filename."""
    if not model_name or model_name in (".", ".."):
        return False
    return Path(model_name).name == model_name


def _load_and_validate(model_name: str) -> Automaton:
    """Path safety, then that `model_name` is an existing model directory,
    then its index.yml via the exact same load_automaton() used at
    boot/upload/delete — raising ValueError on any failure. The one place
    "is this a valid, existing model" is decided; nothing is touched by this
    alone, it only reads. Shared by activate_model() and its idempotent
    PUT .../activate wrapper below, so neither duplicates these checks."""
    if not _is_safe_model_name(model_name):
        raise ValueError(f"Invalid model name: '{model_name}'.")
    model_dir = MODELS_DIR / model_name
    if not model_dir.is_dir():
        raise ValueError(f"Model '{model_name}' does not exist.")
    return load_automaton(model_dir / "index.yml")


async def activate_model(model_name: str, commit: CommitCallback) -> Automaton:
    """Validates `model_name` via _load_and_validate(), then unconditionally
    swaps the active automaton and awaits `commit(new_automaton)` (see
    module docstring). Used directly by delete_model() to fall back to
    "default", and by activate_model_idempotent() below whenever the
    requested model differs from the one already active."""
    new_automaton = _load_and_validate(model_name)

    _set_active(model_name, new_automaton)
    await commit(new_automaton)
    return new_automaton


async def activate_model_idempotent(model_name: str, commit: CommitCallback) -> Automaton:
    """For PUT /api/models/{model_name}/activate: always validates
    `model_name` first via _load_and_validate() — the exact same checks
    activate_model() itself runs — even if it's already the active model;
    idempotency only ever skips the SIDE EFFECTS (swap + the commit()-driven
    session reset), never the correctness checks. Activating the model
    that's already active is therefore a true no-op. A different model is
    activated by delegating to activate_model() itself, reused as-is."""
    new_automaton = _load_and_validate(model_name)
    if model_name == _active_model_name:
        return new_automaton
    return await activate_model(model_name, commit)


def _looks_like_zip(content_type: str | None, content: bytes) -> bool:
    """Format is decided by Content-Type first (a PUT's body has no
    filename to go on). 'zip' anywhere in the media type means zip; 'yaml'/
    'yml' means the lone-file format. Anything else — missing header, or a
    generic type like application/octet-stream — falls back to sniffing the
    zip local-file-header magic number, since that's unambiguous regardless
    of what the client claims."""
    if content_type:
        media_type = content_type.split(";")[0].strip().lower()
        if "zip" in media_type:
            return True
        if "yaml" in media_type or "yml" in media_type:
            return False
    return content[:4] == b"PK\x03\x04"


async def _put_yaml_model(model_name: str, content: bytes, commit: CommitCallback) -> dict:
    """Creates/replaces models/<model_name>/index.yml from a raw YAML body.
    A temp file is written directly inside the target model directory
    (created upfront if missing), so attachment paths already resolve
    correctly during validation. On success only the temp file is renamed to
    index.yml — any attachments already present in that directory from a
    previous PUT are left untouched (a lone YAML body can't carry attachments
    of its own, unlike a zip bundle); on failure, a pre-existing directory is
    left exactly as it was."""
    model_dir = MODELS_DIR / model_name
    dir_preexisted = model_dir.is_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    temp_path = model_dir / f".tmp_{uuid.uuid4().hex}.yml"
    temp_path.write_bytes(content)
    final_path = model_dir / "index.yml"

    try:
        new_automaton = load_automaton(temp_path)
    except Exception as exc:
        # Broad on purpose: any way this file fails to become a usable
        # Automaton (bad YAML, wrong shape, failed semantic validation) is
        # equally "this upload is invalid" from the caller's point of view.
        temp_path.unlink(missing_ok=True)
        if not dir_preexisted:
            try:
                model_dir.rmdir()
            except OSError:
                pass  # not empty (e.g. a concurrent PUT of the same name) — leave it
        return {"success": False, "error": str(exc)}

    temp_path.replace(final_path)

    _set_active(model_name, new_automaton)
    await commit(new_automaton)

    return {"success": True, "model_name": model_name}


def _extract_zip_safely(content: bytes, staging_dir: Path) -> None:
    """Validates zip-slip safety, flatness (no subdirectories), and that
    there's exactly one 'index.yml' at the root with no other .yml/.yaml
    alongside it — ALL before extracting a single file to disk. Raises
    ValueError (or zipfile.BadZipFile, for a corrupt/non-zip upload) on any
    violation."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [entry.replace("\\", "/") for entry in zf.namelist()]
        staging_resolved = staging_dir.resolve()

        for name in names:
            # Zip-slip protection: mandatory before extracting anything.
            if name.startswith("/") or any(part == ".." for part in Path(name).parts):
                raise ValueError(f"Unsafe path inside zip: '{name}'.")
            resolved = (staging_dir / name).resolve()
            if resolved != staging_resolved and staging_resolved not in resolved.parents:
                raise ValueError(f"Unsafe path inside zip: '{name}'.")
            # Flat only: a directory entry or a nested file both contain '/'.
            if "/" in name:
                raise ValueError(f"Zip must be flat (no subdirectories): found '{name}'.")

        index_entries = [n for n in names if n == "index.yml"]
        other_yaml_entries = [
            n for n in names if n != "index.yml" and n.lower().endswith((".yml", ".yaml"))
        ]
        if not index_entries:
            raise ValueError("Zip must contain an 'index.yml' file at its root.")
        if len(index_entries) > 1:
            raise ValueError("Zip contains more than one 'index.yml'.")
        if other_yaml_entries:
            raise ValueError(
                "Zip must contain only one YAML file (index.yml) at its root; "
                f"also found: {', '.join(sorted(other_yaml_entries))}"
            )

        zf.extractall(staging_dir)


async def _put_zip_model(model_name: str, content: bytes, commit: CommitCallback) -> dict:
    """Creates/replaces models/<model_name>/ from a raw zip body: extracted
    into a fresh temp directory (sibling to the eventual model directory, not
    yet under its final name) so attachment paths resolve correctly during
    validation — everything the zip contained is already colocated there. On
    success the whole directory is promoted into place with a single rename,
    replacing any previous model of the same name entirely (unlike the
    lone-YAML PUT, a zip is a complete, self-contained bundle); on failure a
    pre-existing directory of that name is left exactly as it was."""
    staging_dir = MODELS_DIR / f".tmp_{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True)

    try:
        _extract_zip_safely(content, staging_dir)
    except (zipfile.BadZipFile, ValueError) as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return {"success": False, "error": str(exc)}

    index_path = staging_dir / "index.yml"
    final_dir = MODELS_DIR / model_name

    try:
        new_automaton = load_automaton(index_path)
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return {"success": False, "error": str(exc)}

    if final_dir.exists():
        shutil.rmtree(final_dir)
    staging_dir.rename(final_dir)

    _set_active(model_name, new_automaton)
    await commit(new_automaton)

    return {"success": True, "model_name": model_name}


async def put_model(
    model_name: str, content: bytes, content_type: str | None, commit: CommitCallback
) -> dict:
    """For PUT /api/models/{model_name}: creates or fully replaces that
    model from a raw request body. Unlike the old filename-based upload, the
    resource's identity (`model_name`) comes only from the URL — never from
    the body's content or any name embedded in it — so it's validated before
    any filesystem path is built from it. The body's *format* (single YAML
    vs. zip bundle) is a separate, unrelated concern, decided from
    Content-Type with a magic-number fallback (see _looks_like_zip).

    Stages -> validates via load_automaton() (the exact same function used
    at boot and by activate_model()) -> only on success, commits into
    models/<model_name>/, swaps the active automaton, and awaits
    `commit(new_automaton)` — the identical callback activate_model() uses,
    so the reset logic is never duplicated between switch and this endpoint.
    A failed validation leaves the filesystem, the active automaton, and
    (since `commit` is never called) the chat session exactly as they were.
    """
    if not _is_safe_model_name(model_name):
        return {"success": False, "error": f"Invalid model name: '{model_name}'."}

    if _looks_like_zip(content_type, content):
        return await _put_zip_model(model_name, content, commit)
    return await _put_yaml_model(model_name, content, commit)


def export_model_zip(model_name: str) -> bytes:
    """For GET /api/models/{model_name}: the read side of the same
    resource PUT /api/models/{model_name} writes — the zip this returns is
    always accepted back by that endpoint with no transformation, since it's
    built with the exact layout upload/put already requires (flat, index.yml
    at the zip root, attachments alongside it). Always a zip, even for a
    model with no attachments at all, so there's exactly one export format
    to round-trip through PUT.

    Raises FileNotFoundError if `model_name` fails path-safety validation or
    doesn't correspond to an existing model directory — same as
    delete_model(), and deliberately undistinguished for the same reason.
    Not restricted to the active model: any listed model can be exported,
    consistent with DELETE already being general-purpose on this resource.
    """
    if not _is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
        raise FileNotFoundError(f"Model '{model_name}' does not exist.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted((MODELS_DIR / model_name).iterdir()):
            if entry.is_file() and not entry.name.startswith("."):
                zf.write(entry, arcname=entry.name)
    return buffer.getvalue()


async def delete_model(model_name: str, commit: CommitCallback) -> None:
    """For DELETE /api/models/{model_name}: removes models/<model_name>/
    from disk. Deliberately generalized beyond "delete the active model" —
    any listed model can be deleted, active or not, e.g. to clean up a model
    uploaded in the past without first having to switch to it.

    Raises FileNotFoundError if `model_name` fails path-safety validation or
    doesn't correspond to an existing model directory — both are "this
    resource doesn't exist" from the caller's point of view, deliberately
    undistinguished. Raises PermissionError if `model_name` is the default
    model: enforced here unconditionally, the same way e.g. final states are
    enforced server-side in chat_ws rather than trusted to the frontend. Lets
    any OSError from the actual filesystem removal propagate as-is.

    Only if `model_name` was the active model does this have any further
    effect: it reactivates "default" via activate_model() (the same function
    switch/put already use, including its commit()-driven session reset) —
    deleting an unused model must never touch the in-memory automaton or DB.
    """
    if not _is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
        raise FileNotFoundError(f"Model '{model_name}' does not exist.")
    if model_name == DEFAULT_MODEL_NAME:
        raise PermissionError("The default model cannot be deleted.")

    shutil.rmtree(MODELS_DIR / model_name)

    if model_name == _active_model_name:
        await activate_model(DEFAULT_MODEL_NAME, commit)
