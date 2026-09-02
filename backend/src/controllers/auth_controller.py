"""LoginView.vue's own backend surface — login/logout and the current
user, per the auth-service section-0 config and the auth middleware
(main.py) that gates every other route.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException, Request, Response

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from schemas import AcceptTermsRequest, LoginRequest, SetWhatsAppPhoneNumberRequest
from session import Session

from .base_controller import BaseController, get, post, put

# Public, static — no auth needed to read it (a rejected/pending identity
# still needs to see it before deciding).
TERMS_PATH = Path(__file__).resolve().parent.parent / "docs" / "TERMS.md"


class AuthController(BaseController):

    def __init__(self, auth_service: AuthService) -> None:
        self.auth_service = auth_service

    @get("/api/auth/providers", role=None)
    def get_providers(self):
        return {"providers": self.auth_service.public_providers()}

    @get("/api/auth/terms", role=None)
    def get_terms(self):
        return {"content": TERMS_PATH.read_text(encoding="utf-8")}

    @post("/api/auth/login", role=None)
    def post_login(self, req: LoginRequest, response: Response):
        try:
            token = self.auth_service.login(req.provider, req.credential)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=int(self.auth_service.token_ttl.total_seconds()),
        )
        return {"success": True}

    @post("/api/auth/accept-terms", role="pending")
    def post_accept_terms(self, request: Request, req: AcceptTermsRequest):
        """TermsView.vue's Accept button — creates the User row that
        login() deliberately deferred. Reads the session cookie straight
        off the request (rather than Session(), which only carries email/
        role) since AuthService.complete_registration needs the full
        identity the token already has: provider/provider_user_id/name/
        picture_url. req.invite_code is the invite that registration must
        carry (see AcceptTermsRequest) — a PermissionError means it didn't
        (invalid/expired/maxed-out code), surfaced as 403 with the
        specific reason rather than the 401 an actually-bad/expired
        session token gets."""
        token = request.cookies.get(SESSION_COOKIE_NAME)
        try:
            self.auth_service.complete_registration(token, req.invite_code)
        except PermissionError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail=str(exc)) from exc
        return {"success": True}

    # role="pending": App.vue's own TermsView-vs-InviteRequiredView gate
    # needs this before ever calling accept-terms, for an identity that
    # has no User row yet — same reachability as accept-terms/logout.
    @get("/api/auth/pending-status", role="pending")
    def get_pending_status(self):
        return {"invite_exempt": self.auth_service.is_invite_exempt(Session().user)}

    # role="pending": logout must stay reachable by an identity that
    # rejected the Terms screen and never got a User row at all — not
    # just by fully registered ones.
    @post("/api/auth/logout", role="pending")
    def post_logout(self, response: Response):
        response.delete_cookie(key=SESSION_COOKIE_NAME)
        return {"success": True}

    @get("/api/auth/me")
    def get_me(self):
        """The auth middleware already validated the cookie for this
        request to have reached here at all — Session().user is the
        email it resolved. Serves both the topbar avatar and
        ProfileView.vue, the only two consumers of the current user's
        own profile data."""
        return self.auth_service.get_profile(Session().user)

    @put("/api/auth/me/whatsapp-phone-number")
    def put_whatsapp_phone_number(self, req: SetWhatsAppPhoneNumberRequest):
        try:
            return self.auth_service.set_whatsapp_phone_number(Session().user, req.phone_number, req.confirm_merge)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/auth/erase-data")
    def post_erase_data(self, response: Response):
        """ProfileView.vue's "Erase all my data" — deletes the User row
        and everything tied to it (see AuthService.erase_account), then
        clears the cookie itself so the now-nonexistent identity can't
        make another request even if the frontend's own follow-up logout
        call never lands."""
        self.auth_service.erase_account(Session().user)
        response.delete_cookie(key=SESSION_COOKIE_NAME)
        return {"success": True}
