"""Invite lifecycle for "share project" links (see ShareProjectDialog.vue/
shareLink.js) — the one place project-invite business rules live, so no
other service (AuthService included) reaches into Db.*invite* directly.
Each dialog-open creates a fresh Invite row (see ProjectService.create_invite),
backed by a short random code, its own expiry, and its own max-shares
budget; a code's actual redemption count is never stored on the row
itself, always counted live off UserProject.invite (see
Db.count_invite_redemptions)."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta

from db import Db, _utc_iso

_CODE_ALPHABET = string.ascii_letters + string.digits
_CODE_LENGTH = 6
_MAX_CODE_ATTEMPTS = 10


class InviteManager:
    def __init__(self, db: Db, valid_days: int, max_shares: int) -> None:
        self._db = db
        self._valid_days = valid_days
        self._max_shares = max_shares

    def _generate_unique_code(self) -> str:
        for _ in range(_MAX_CODE_ATTEMPTS):
            code = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
            if self._db.get_invite_by_code(code) is None:
                return code
        raise RuntimeError("Could not generate a unique invite code.")

    def create_invite(self, project_name: str, created_by: str | None) -> dict:
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"No such project: {project_name!r}")
        code = self._generate_unique_code()
        expires_at = datetime.utcnow() + timedelta(days=self._valid_days)
        invite = self._db.create_invite(code, project_name, created_by, expires_at, self._max_shares)
        return self._payload(invite)

    def get_project_name_by_code(self, code: str) -> str | None:
        """Existence-only resolution — an already-registered identity
        following the link just needs to land on the right project (see
        useAppBoot.js's activateInvitedProject), regardless of whether
        the invite is expired or maxed out: those budgets gate *new*
        registrations only (see validate_for_registration below), never
        someone who's already in."""
        invite = self._db.get_invite_by_code(code) if code else None
        return invite.project_name_id if invite is not None else None

    def validate_for_registration(self, code: str | None):
        """The gate a brand-new identity's self-registration must clear
        (see AuthService.complete_registration) — raises PermissionError
        with a reason a caller can surface directly to the person trying
        to register. Returns the Invite row on success, for redeem()
        below to record against."""
        invite = self._db.get_invite_by_code(code) if code else None
        if invite is None:
            raise PermissionError("This invite link is invalid.")
        if invite.expires_at < datetime.utcnow():
            raise PermissionError("This invite link has expired.")
        if self._db.count_invite_redemptions(invite.id) >= invite.max_shares:
            raise PermissionError("This invite link has already reached its maximum number of uses.")
        return invite

    def redeem(self, invite, user_id: str) -> None:
        """Records that `user_id` registered through `invite` — the other
        half of what makes count_invite_redemptions (and so
        validate_for_registration's own max-shares check) mean anything."""
        self._db.record_invite_redemption(user_id, invite.project_name_id, invite.id, datetime.utcnow())

    @staticmethod
    def _payload(invite) -> dict:
        return {
            "code": invite.code,
            "expires_at": _utc_iso(invite.expires_at),
            "max_shares": invite.max_shares,
        }
