import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The "Auto" tab's own replay launch — sessionId null means the
// whole-project-scope run (every labeled session at once). `username`,
// when given, scopes that whole-project run to just that user's sessions
// instead of the requesting user's own.
export function postTest(projectName, sessionId, strategy, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, strategy, ...(username != null ? { username } : {}) })
  })
}

export function getTest(projectName, testId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests/${encodeURIComponent(testId)}`)
}

export function getTests(projectName, sessionId, username) {
  const params = new URLSearchParams()
  if (sessionId != null) params.set('session_id', sessionId)
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests${query}`)
}

export function deleteTests(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests`, { method: 'DELETE' })
}

export function getTestMetrics(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests/metrics`)
}

// Every real state key of the project's current draft automaton.
export function getProjectStates(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states`)
}

export function postStateTest(projectName, stateKey, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateKey)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function getJobsStatus(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/jobs-status?strategy=${encodeURIComponent(strategy)}`)
}

export function getAggregateResult(projectName, kind, target, strategy) {
  const params = new URLSearchParams({ kind, strategy })
  if (target != null) params.set('target', target)
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/aggregate-result?${params}`)
}

export function postStatesAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalsAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postRootAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/root/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUsersAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSessionsRun(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUserSessionsRun(projectName, username, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/${encodeURIComponent(username)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalTest(projectName, signalName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}
