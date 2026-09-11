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

export function postCloseSession(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/close`, { method: 'POST' })
}

// The transcript as it already is. Never opens the conversation — a
// session nobody has started yet reads as empty rather than running its
// opening turn under the reader (see turn/turn_service.py's own
// read_transcript, and get_messages for the one that does open it).
export function getTranscript(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/transcript`)
}

export function getSessionState(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/state`)
}

export function createChatSocket() {
  return new WebSocket(WS_URL)
}

// Firing an action on a session that has no channel of its own — the
// editor's Test chat, the app store's preview. A live session refuses
// this route: the write asks who is speaking and this caller cannot say
// (see turn/session_controller.py, and postChatWindowAction for the one
// that can).
export function postSessionAction(actionName, sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  })
}

export function putSessionAudio(sessionId, enabled) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/audio`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
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

// Sets (reaction given) or clears (null) the user's own reaction to a bot
// message — a key out of the active project's own `reactions` dict (see
// chatStore.js's state.reactions).
export function putMessageReaction(messageId, reaction) {
  return apiFetch(`${API_URL}/core/messages/${encodeURIComponent(messageId)}/reaction`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reaction })
  })
}
