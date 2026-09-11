import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
// The bus channel is a route under API_URL like any other, so it is
// derived from it rather than configured a second time: a VITE_WS_URL
// that could disagree with VITE_API_URL about host or origin was a way
// to point the two halves of one backend at different servers.
const WS_URL = new URL(API_URL + '/core/bus', location.href).href.replace(/^http/, 'ws')

export function getCurrentSession(sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/webchat/sessions/current${query}`)
}

export function postCreateSession() {
  return apiFetch(`${API_URL}/skills/webchat/sessions`, { method: 'POST' })
}

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

export function getSessions(projectId, includeImported = false) {
  const query = includeImported ? '?include_imported=true' : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/sessions${query}`)
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

export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export function postCloseSession(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/close`, { method: 'POST' })
}

export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/messages`)
}

export function getSessionState(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/state`)
}

// HumanOperatorChatView.vue's own state read — see ChatService.
// get_state_for_operator: every action is manually triggerable while an
// operator is attached, regardless of this session's own is_auto_tracking_
// enabled flag, and this is never sent to the customer's own chat.
export function getOperatorState(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/operator-state`)
}

export function createChatSocket() {
  return new WebSocket(WS_URL)
}

export function postAction(actionName, sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  })
}

export function putSessionAudio(sessionId, enabled) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/audio`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
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

export function getActuators(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/actuators`)
}

export function putActuators(sessionId, enabled) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/actuators`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}

// "Restart from here": deletes every message (and its Signals rows) at
// or after `timestamp` in `sessionId`, rolling state back to what it was
// immediately before. `timestamp` must be a backend-issued ISO string.
export function postTruncateSession(sessionId, timestamp) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
  })
}
