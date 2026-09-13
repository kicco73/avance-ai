import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getMetrics(projectId, messageId, full, username) {
  const params = new URLSearchParams()
  if (messageId != null) params.set('message_id', messageId)
  if (full) params.set('full', 'true')
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/metrics${query}`)
}

export function getUserLatestSignals(projectId, username) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/latest-signals`)
}

export function getTimeline(projectId, username) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/timeline`)
}

export function getMetricsHistory(projectId, username) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/metrics-history`)
}
