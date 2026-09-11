import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'


// Sets (expectedState given) or clears (null) messageId's expert-
// annotated expected state. 409 if messageId isn't an evaluation point,
// 422 for an unknown state.
export function putMessageExpectedState(messageId, expectedState) {
  return apiFetch(`${API_URL}/skills/platform/messages/${encodeURIComponent(messageId)}/expected-state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_state: expectedState })
  })
}

// Sets or clears messageId's expert-annotated expected signal values.
// `expectedValues` is the whole replacement dict (a signal name missing
// from it is cleared for that signal alone); null/{} clears every signal.
export function putMessageExpectedSignals(messageId, expectedValues) {
  return apiFetch(`${API_URL}/skills/platform/messages/${encodeURIComponent(messageId)}/expected-signals`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_values: expectedValues })
  })
}

// Sets or clears messageId's expert-left free-text comment. Unlike
// putMessageExpectedState/putMessageExpectedSignals, every message is a
// valid target (no 409 for "not an evaluation point").
export function putMessageComment(messageId, comment) {
  return apiFetch(`${API_URL}/skills/platform/messages/${encodeURIComponent(messageId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}


// Sets/clears a session's persisted "reviewed by a domain expert" flag —
// the source of truth for has_annotations. A toggle: calling with
// `false` un-marks it again.
export function putSessionLabeled(sessionId, labeled) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/labeled`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ labeled })
  })
}

// Renames a session; null (or blank) clears it back to unset. Returns
// the same session payload putSessionLabeled does, so the Sessions panel
// row can be refreshed directly from the response.
export function putSessionTitle(sessionId, title) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/title`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title })
  })
}

// Sets or clears a session-wide free-text note — the "Label sessions"
// view's own Info tab, distinct from putMessageComment's per-message one.
export function putSessionComment(sessionId, comment) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}

// Clears every expert annotation (expected_state and expected_values
// alike) across sessionId's Signals rows in one call.
export function deleteSessionAnnotations(sessionId) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/annotations`, {
    method: 'DELETE'
  })
}
