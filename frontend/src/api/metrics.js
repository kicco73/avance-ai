import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// `messageId`, when given, computes metrics as of that exact message's
// own timestamp instead of the live/current history — see the
// "Label sessions" view's point-in-time Inspector. `full`: every core
// metric, including ones that need more than one session (e.g.
// Retention/Activity Consistency) instead of the usual "one_session"
// subset. `username`, when given, computes metrics for that user's own
// sessions instead of the caller's — see Manage Users' own statistics panel.
export function getMetrics(projectId, messageId, full, username) {
  const params = new URLSearchParams()
  if (messageId != null) params.set('message_id', messageId)
  if (full) params.set('full', 'true')
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/metrics${query}`)
}

export function getUserLatestSignals(projectId, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/latest-signals`)
}

export function getTimeline(projectId, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/timeline`)
}

export function getMetricsHistory(projectId, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/metrics-history`)
}
