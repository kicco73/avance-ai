import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The "Auto" tab's own replay launch — sessionId null means the
// whole-project-scope run (every labeled session at once). `username`,
// when given, scopes that whole-project run to just that user's sessions
// instead of the requesting user's own.
export function postTest(projectId, sessionId, strategy, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, strategy, ...(username != null ? { username } : {}) })
  })
}

export function getTest(projectId, testId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests/${encodeURIComponent(testId)}`)
}

export function getTests(projectId, sessionId, username) {
  const params = new URLSearchParams()
  if (sessionId != null) params.set('session_id', sessionId)
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests${query}`)
}

export function deleteTests(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests`, { method: 'DELETE' })
}

export function deleteTestJob(projectId, jobKey) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests/jobs/${encodeURIComponent(jobKey)}`, { method: 'DELETE' })
}

export function deleteAllTestJobs(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests/jobs`, { method: 'DELETE' })
}

export function getTestMetrics(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/tests/metrics`)
}

// Every real state key of the project's current draft automaton.
export function getProjectStates(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/states`)
}

export function postStateTest(projectId, stateKey, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateKey)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function getAggregateResult(projectId, kind, target, strategy) {
  const params = new URLSearchParams({ kind, strategy })
  if (target != null) params.set('target', target)
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/aggregate-result?${params}`)
}

export function postStatesAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/states/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalsAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/signals/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postRootAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/root/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUsersAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/users/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSessionsRun(projectId, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/sessions/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUserSessionsRun(projectId, username, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/users/${encodeURIComponent(username)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalTest(projectId, signalName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}
