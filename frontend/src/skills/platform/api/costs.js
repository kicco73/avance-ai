import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
export function getCostApps() {
  return apiFetch(`${API_URL}/skills/platform/costs/apps`)
}

export function getCostUsers(projectId) {
  return apiFetch(`${API_URL}/skills/platform/costs/apps/${encodeURIComponent(projectId)}/users`)
}

export function getCostSessions(projectId, username) {
  return apiFetch(`${API_URL}/skills/platform/costs/apps/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/sessions`)
}

export function getCostSeries({ projectId, scope, username = null, sessionId = null }) {
  const params = new URLSearchParams({ project_id: projectId, scope })
  if (username !== null) params.set('username', username)
  if (sessionId !== null) params.set('session_id', String(sessionId))
  return apiFetch(`${API_URL}/skills/platform/costs/series?${params}`)
}

export function getCostTurns({ projectId, scope }) {
  const params = new URLSearchParams({ project_id: projectId, scope })
  return apiFetch(`${API_URL}/skills/platform/costs/turns?${params}`)
}
