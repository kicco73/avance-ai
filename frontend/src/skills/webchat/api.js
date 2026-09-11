import { apiFetch } from '../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getCurrentSession(sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/webchat/sessions/current${query}`)
}

export function postCreateSession() {
  return apiFetch(`${API_URL}/skills/webchat/sessions`, { method: 'POST' })
}

export function postAction(actionName, sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  })
}

export function getOperatorState(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/operator-state`)
}

// Reading the chat window's own history, which opens the conversation if
// it has not started: the opening message is a real turn, so this asks
// to be on a channel and says which. getTranscript is the read that is
// only a read.
export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/skills/webchat/sessions/${encodeURIComponent(sessionId)}/messages`)
}
