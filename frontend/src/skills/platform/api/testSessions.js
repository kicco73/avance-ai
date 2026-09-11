import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The editor's embedded "Test" chat: its own session pool, against the
// draft revision rather than the published one, plus the dev-mode switch
// that freezes automatic transitions.
//
// IMPORTANT — these are platform's routes, called from a core file, and
// they belong in skills/platform/. They cannot go there until
// chatStoreFactory receives them injected the way it already receives
// postAction and getMessages: the caller is testChatStore, which is
// platform's too, and so is aiModelStore next door for the same reason.

// EditProjectView's embedded "Test" chat — the one place a session can
// exist against an unpublished revision. Which revision applies is
// decided by which endpoint is called, never by a caller-supplied flag.
export function getCurrentTestSession(sessionId, projectId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/test-sessions/current${query}`)
}

export function postCreateTestSession(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/test-sessions`, { method: 'POST' })
}

// EditProjectView's embedded "Test" chat's own Sessions panel — a
// separate list from getSessions: a "Test" session never appears there,
// and a real one never appears here.
export function getTestSessions(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/test-sessions`)
}

export function postResetTestSessions(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/test-sessions/reset`, { method: 'POST' })
}

// "Dev mode: freeze automatic state transitions" — EditProjectView's
// embedded "Test" chat only, per test session, never global.
export function getAutoTracking(sessionId) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/autotracking`)
}

export function putAutoTracking(sessionId, enabled) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/autotracking`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}
