import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getSignals() {
  return apiFetch(`${API_URL}/skills/platform/inspector/signals`)
}


export function getEnv(sessionId, messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/env${query}`)
}

export function getOutput(sessionId, messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/output${query}`)
}

export function putEnvValue(sessionId, key, value) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/env/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  })
}

export function deleteEnvValue(sessionId, key) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/env/${encodeURIComponent(key)}`, {
    method: 'DELETE'
  })
}

export function clearEnv(sessionId) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/env`, {
    method: 'DELETE'
  })
}
