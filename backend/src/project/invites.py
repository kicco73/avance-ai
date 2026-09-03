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
from urllib.parse import quote

from db import Db, _utc_iso

_CODE_ALPHABET = string.ascii_letters + string.digits
_CODE_LENGTH = 6
_MAX_CODE_ATTEMPTS = 10


class InviteManager:
    def __init__(
        self, db: Db, valid_days: int, max_shares: int, whatsapp_number: str | None = None,
        whatsapp_invite_prefix: str = "Invitation code: ",
    ) -> None:
        self._db = db
        self._valid_days = valid_days
        self._max_shares = max_shares
        self._whatsapp_number = whatsapp_number
        self._whatsapp_invite_prefix = whatsapp_invite_prefix

    def _generate_unique_code(self) -> str:
        for _ in range(_MAX_CODE_ATTEMPTS):
            code = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
            if self._db.get_invite_by_code(code) is None:
                return code
        raise RuntimeError("Could not generate a unique invite code.")

    def create_invite(self, project_id: str, created_by: str | None) -> dict:
        if not self._db.project_exists(project_id):
            raise FileNotFoundError(f"No such project: {project_id!r}")
        self._db.delete_expired_unredeemed_invites()
        code = self._generate_unique_code()
        expires_at = datetime.utcnow() + timedelta(days=self._valid_days)
        invite = self._db.create_invite(code, project_id, created_by, expires_at, self._max_shares)
        return self._payload(invite)

    def resolve_invite_link(self, code: str | None, user_id: str, role: str) -> str | None:
        """Where an already-authenticated identity following a share link
        lands (see useAppBoot.js's activateInvitedProject) — unlike
        validate_for_registration below (a brand-new identity's own
        self-registration), a code that's simply unknown here is a
        graceful no-op, same as before. role='user' is the only one ever
        gated by UserProject (see Db.list_projects_with_availability_for_user),
        so a revisit (already has a UserProject row for this project) or
        any other role never spends the invite's budget or writes a row —
        only the first time a 'user' actually reaches a project through
        this code does the same expiry/max-shares check
        validate_for_registration enforces apply here too, followed by
        the same redemption."""
        invite = self._db.get_invite_by_code(code) if code else None
        if invite is None:
            return None
        project_id = invite.project_id
        if role != 'user' or self._db.user_has_project_access(user_id, project_id):
            return project_id
        self._ensure_within_budget(invite)
        self.redeem(invite, user_id)
        return project_id

    def _ensure_within_budget(self, invite) -> None:
        if invite.expires_at < datetime.utcnow():
            raise PermissionError("This invite link has expired.")
        if self._db.count_invite_redemptions(invite.id) >= invite.max_shares:
            raise PermissionError("This invite link has already reached its maximum number of uses.")

    def validate_for_registration(self, code: str | None):
        """The gate a brand-new identity's self-registration must clear
        (see AuthService.complete_registration) — raises PermissionError
        with a reason a caller can surface directly to the person trying
        to register. Returns the Invite row on success, for redeem()
        below to record against."""
        invite = self._db.get_invite_by_code(code) if code else None
        if invite is None:
            raise PermissionError("This invite link is invalid.")
        self._ensure_within_budget(invite)
        return invite

    def redeem(self, invite, user_id: str) -> None:
        """Records that `user_id` registered through `invite` — the other
        half of what makes count_invite_redemptions (and so
        validate_for_registration's own max-shares check) mean anything."""
        self._db.record_invite_redemption(user_id, invite.project_id, invite.id, datetime.utcnow())

    def _payload(self, invite) -> dict:
        return {
            "code": invite.code,
            "expires_at": _utc_iso(invite.expires_at),
            "max_shares": invite.max_shares,
            "whatsapp_url": self._whatsapp_url(invite.code),
        }

    def _whatsapp_url(self, code: str) -> str | None:
        if not self._whatsapp_number:
            return None
        return f"https://wa.me/{self._whatsapp_number}?text={quote(self._whatsapp_invite_prefix + code)}"
