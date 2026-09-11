import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// Which projects this caller can see, and which one is active. Core:
// a session has to know what it is talking about before it can talk,
// whether or not an editor was installed to change it.
export function getProjects() {
  return apiFetch(`${API_URL}/core/projects`)
}

export function activateProject(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
}

// The landing half of the "share project" invite-link flow (see
// shareLink.js): resolves a scanned code back to its project, and for a
// user reaching it for the first time grants access. Core because the
// link is the product's own front door — whoever opens a shared
// conversation has no editor in the picture.
export function postRedeemInviteCode(code) {
  return apiFetch(`${API_URL}/core/projects/invitations/${encodeURIComponent(code)}`, { method: 'POST' })
}

// The bytes of one of a project's own files. Core, and role-free: the
// chat window loads its skin (index.css and whatever it references)
// through this on every live session, so a product built without an
// editor still has to be able to draw itself.
export function projectFileContentUrl(projectId, fileName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return `${API_URL}/core/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/content${query}`
}

// The full Signals event log for a session (snapshots + transitions,
// chronological) — for the "Label sessions" view's timeline.
export function getSessionSignals(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/signals`)
}

// { tokens: number | null } — estimated input-token cost of `stateKey`'s
// own turn prompt (attachments, signal/reaction definitions, env, ...),
// null when no AiService is configured. `sessionId`: see getProjectGraph above.
export function getStateInputTokens(projectId, stateKey, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateKey)}/tokens${query}`)
}

// `stateKey`, when given, scopes each signal's `relevant` field to that
// state's outgoing actions; omitted, every state's triggers combine
// instead. `sessionId`: see getProjectGraph above.
export function getProjectSignals(projectId, stateKey, sessionId) {
  const params = new URLSearchParams()
  if (stateKey != null) params.set('state_key', stateKey)
  if (sessionId != null) params.set('session_id', sessionId)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/signals${query}`)
}

export function getUsers() {
  return apiFetch(`${API_URL}/core/users`)
}

// Raw markdown content of a fixed reference doc, backing each "(?)" doc
// button. `name` is one of 'project-specs' / 'metrics' / 'benchmark'.
export function getDoc(name) {
  return apiFetch(`${API_URL}/core/docs/${encodeURIComponent(name)}`)
}

export function getProjectStates(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/states`)
}

// `projectId`'s identifier registry — {identifier: description} per
// namespace (signal, env, system, session, metric, ...) a trigger/env
// expression can reference. Used by TriggerEditor's autocomplete.
export function getIdentifiers(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/identifiers`)
}

// Every file type a project can carry, straight from the backend's own
// catalog (automaton.file_types) — see projectFileTypes.js.
export function getProjectFileTypes() {
  return apiFetch(`${API_URL}/core/projects/file-types`)
}
