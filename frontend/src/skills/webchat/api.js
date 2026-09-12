import { apiFetch } from '../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getCurrentSession(sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/webchat/sessions/current${query}`)
}

export function postCreateSession() {
  return apiFetch(`${API_URL}/skills/webchat/sessions`, { method: 'POST' })
}

export function getOperatorState(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/operator-state`)
}

// The chat window's own history. A read and nothing else: what a
// conversation opens with arrives because the window said `session.new`
// (see backend docs/BUS.md), never because somebody asked what had been
// said. This route stays webchat's own because a chat session belongs to
// the channel that opened it.
export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/messages`)
}
