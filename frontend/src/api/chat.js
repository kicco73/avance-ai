import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
// The bus channel is a route under API_URL like any other, so it is
// derived from it rather than configured a second time: a VITE_WS_URL
// that could disagree with VITE_API_URL about host or origin was a way
// to point the two halves of one backend at different servers.
const WS_URL = new URL(API_URL + '/core/bus', location.href).href.replace(/^http/, 'ws')



export function getSessions(projectId, includeImported = false) {
  const query = includeImported ? '?include_imported=true' : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/sessions${query}`)
}



export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

// A session named outright — one picked out of the sessions panel, or
// one being annotated. A read and nothing else: what a conversation is
// showing right now arrives on `session.messages` because the chat
// entered it (see backend docs/BUS.md), so a session nobody has started
// yet reads as empty rather than running its opening turn under whoever
// looked.
export function getHistory(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/history`)
}

export function createChatSocket() {
  return new WebSocket(WS_URL)
}

export function getActuators(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/actuators`)
}

export function putActuators(sessionId, enabled) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/actuators`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}

// "Restart from here": deletes every message (and its Signals rows) at
// or after `timestamp` in `sessionId`, rolling state back to what it was
// immediately before. `timestamp` must be a backend-issued ISO string.
export function postTruncateSession(sessionId, timestamp) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
  })
}
