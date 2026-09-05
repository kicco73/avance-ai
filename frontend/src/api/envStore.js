import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getSignals() {
  return apiFetch(`${API_URL}/chat/signals`)
}

// `projectId`'s identifier registry — {identifier: description} per
// namespace (signal, env, system, session, metric, ...) a trigger/env
// expression can reference. Used by TriggerEditor's autocomplete.
export function getIdentifiers(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/identifiers`)
}

// `sessionId`'s own "environment" memory: {stored, action_set}, reported
// separately so the Env tab knows which section each value belongs in.
// `messageId` restricts to values as of that message (a test/preview
// session is always current — it keeps no history to look back through).
export function getEnv(sessionId, messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/env${query}`)
}

// Edits (or adds) one stored env key — always current, there's no editing
// history. Returns the same {stored, action_set} shape as getEnv.
export function putEnvValue(sessionId, key, value) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/env/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  })
}

export function deleteEnvValue(sessionId, key) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/env/${encodeURIComponent(key)}`, {
    method: 'DELETE'
  })
}

// Wipes every stored and action-set env key at once. Returns the same
// {stored, action_set} shape as getEnv.
export function clearEnv(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/env`, {
    method: 'DELETE'
  })
}
