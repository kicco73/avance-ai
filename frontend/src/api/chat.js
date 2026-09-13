import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = new URL(API_URL + '/core/bus', location.href).href.replace(/^http/, 'ws')



export function getSessions(projectId, includeImported = false) {
  const query = includeImported ? '?include_imported=true' : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/sessions${query}`)
}



export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

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

export function postTruncateSession(sessionId, timestamp) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
  })
}
