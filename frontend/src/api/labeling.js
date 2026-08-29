import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The full Signals event log for a session (snapshots + transitions,
// chronological) — for the "Label sessions" view's timeline.
export function getSessionSignals(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/signals`)
}

// Sets (expectedState given) or clears (null) messageId's expert-
// annotated expected state. 409 if messageId isn't an evaluation point,
// 422 for an unknown state.
export function putMessageExpectedState(messageId, expectedState) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/expected-state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_state: expectedState })
  })
}

// Sets or clears messageId's expert-annotated expected signal values.
// `expectedValues` is the whole replacement dict (a signal name missing
// from it is cleared for that signal alone); null/{} clears every signal.
export function putMessageExpectedSignals(messageId, expectedValues) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/expected-signals`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_values: expectedValues })
  })
}

// Sets or clears messageId's expert-left free-text comment. Unlike
// putMessageExpectedState/putMessageExpectedSignals, every message is a
// valid target (no 409 for "not an evaluation point").
export function putMessageComment(messageId, comment) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}

// Sets (reaction given) or clears (null) the user's own reaction to a bot
// message — a key out of the active project's own `reactions` dict (see
// chatStore.js's state.reactions).
export function putMessageReaction(messageId, reaction) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/reaction`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reaction })
  })
}

// Sets/clears a session's persisted "reviewed by a domain expert" flag —
// the source of truth for has_annotations. A toggle: calling with
// `false` un-marks it again.
export function putSessionLabeled(sessionId, labeled) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/labeled`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ labeled })
  })
}

// Renames a session; null (or blank) clears it back to unset. Returns
// the same session payload putSessionLabeled does, so the Sessions panel
// row can be refreshed directly from the response.
export function putSessionTitle(sessionId, title) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/title`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title })
  })
}

// Sets or clears a session-wide free-text note — the "Label sessions"
// view's own Info tab, distinct from putMessageComment's per-message one.
export function putSessionComment(sessionId, comment) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}

// Clears every expert annotation (expected_state and expected_values
// alike) across sessionId's Signals rows in one call.
export function deleteSessionAnnotations(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/annotations`, {
    method: 'DELETE'
  })
}
