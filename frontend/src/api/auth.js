import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getState(signal) {
  return apiFetch(`${API_URL}/state`, { signal })
}

// `credential` is the Google Identity Services ID token JWT off the
// "Sign in with Google" callback — see LoginView.vue. The backend sets
// the session cookie itself (Set-Cookie on this response); there's
// nothing in the returned body the caller needs to store.
export function postLogin(provider, credential) {
  return apiFetch(`${API_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, credential })
  })
}

export function postLogout() {
  return apiFetch(`${API_URL}/auth/logout`, { method: 'POST' })
}

// Raw markdown content of the Terms of Service — reachable even by a
// pending (not-yet-registered) or fully anonymous session, since
// TermsView.vue must show it before there's any account to gate on.
export function getTerms() {
  return apiFetch(`${API_URL}/auth/terms`)
}

// TermsView.vue's Accept action — creates the User row postLogin's own
// login() deliberately deferred (see auth_service.py). Registration is
// invite-only: `inviteCode` (see shareLink.js's peekInviteCode) must
// clear the backend's own exists/not-expired/under-max-shares check, or
// this is refused (403, with a specific reason) — see useAppBoot.js's
// handleTermsAccept, the only caller.
export function postAcceptTerms(inviteCode) {
  return apiFetch(`${API_URL}/auth/accept-terms`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ invite_code: inviteCode ?? null })
  })
}

export function getLegalTermsStatus(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/legal-terms-status`)
}

export function postAcceptProjectTerms(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/accept-terms`, { method: 'POST' })
}

// ProfileView.vue's "Erase all my data" — deletes the account and
// everything tied to it server-side (see Db.erase_user_data); also
// clears the session cookie itself.
export function postEraseData() {
  return apiFetch(`${API_URL}/auth/erase-data`, { method: 'POST' })
}

export function getMe() {
  return apiFetch(`${API_URL}/auth/me`)
}

export function getAuthProviders() {
  return apiFetch(`${API_URL}/auth/providers`)
}
