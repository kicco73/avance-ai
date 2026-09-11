import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The "Auto" tab's own replay launch — sessionId null means the
// whole-project-scope run (every labeled session at once). `username`,
// when given, scopes that whole-project run to just that user's sessions
// instead of the requesting user's own.
export function postTest(projectId, sessionId, strategy, username) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, strategy, ...(username != null ? { username } : {}) })
  })
}

export function getTest(projectId, testId) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests/${encodeURIComponent(testId)}`)
}

export function getTests(projectId, sessionId, username) {
  const params = new URLSearchParams()
  if (sessionId != null) params.set('session_id', sessionId)
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests${query}`)
}

export function deleteTests(projectId) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests`, { method: 'DELETE' })
}

export function deleteTestJob(projectId, jobKey) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests/jobs/${encodeURIComponent(jobKey)}`, { method: 'DELETE' })
}

export function deleteAllTestJobs(projectId) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests/jobs`, { method: 'DELETE' })
}

export function getTestMetrics(projectId) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/tests/metrics`)
}

// Every real state key of the project's current draft automaton.
export function getProjectStates(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/states`)
}

export function postStateTest(projectId, stateKey, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/runs/states/${encodeURIComponent(stateKey)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function getAggregateResult(projectId, kind, target, strategy) {
  const params = new URLSearchParams({ kind, strategy })
  if (target != null) params.set('target', target)
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/aggregations/result?${params}`)
}

export function postStatesAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/aggregations/states`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalsAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/aggregations/signals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postRootAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/aggregations/root`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUsersAggregation(projectId, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/aggregations/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSessionsRun(projectId, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/runs/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUserSessionsRun(projectId, username, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/runs/users/${encodeURIComponent(username)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalTest(projectId, signalName, strategy) {
  return apiFetch(`${API_URL}/skills/testing/projects/${encodeURIComponent(projectId)}/runs/signals/${encodeURIComponent(signalName)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}
