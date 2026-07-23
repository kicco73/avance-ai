import { setApiError } from './errorStore.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

// The one place every REST call goes through: on a non-2xx response (or a
// network-level failure), writes {message, detail} into the shared error
// area (see errorStore.js) before throwing, so no caller has to handle
// display itself. `parse: 'blob'` is for downloadModel's zip body — every
// other endpoint returns JSON on success.
async function apiFetch(url, options, { parse = 'json' } = {}) {
  let res
  try {
    res = await fetch(url, options)
  } catch (err) {
    if (err.name === 'AbortError') throw err // caller-driven timeout/cancel, not a user-facing failure
    setApiError('Unable to reach the backend.', err.message)
    throw err
  }

  if (!res.ok) {
    let message = `Error ${res.status}`
    let detail = ''
    try {
      const body = await res.json()
      if (body?.error?.message) {
        message = body.error.message
        detail = body.error.detail ?? ''
      }
    } catch {
      // ignore non-JSON body
    }
    setApiError(message, detail)
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
    throw err
  }

  return parse === 'blob' ? res.blob() : res.json()
}

// Also used as the initial-boot ping (see App.vue): `signal` lets that
// caller bound each attempt with a timeout, since a plain fetch() never
// times out on its own against a hung connection.
export function getState(signal) {
  return apiFetch(`${API_URL}/state`, { signal })
}

export function getMessages() {
  return apiFetch(`${API_URL}/messages`)
}

// Chat runs over a websocket: the backend pushes status updates (retrying,
// done, error) as they happen instead of the client polling for them.
export function createChatSocket() {
  return new WebSocket(WS_URL)
}

export function getSignals() {
  return apiFetch(`${API_URL}/signals`)
}

export function postAction(actionName) {
  return apiFetch(`${API_URL}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  })
}

export function getAutoTracking() {
  return apiFetch(`${API_URL}/autotracking`)
}

export function postAutoTracking(enabled) {
  return apiFetch(`${API_URL}/autotracking`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}

export function postTriggersPreview(signals) {
  return apiFetch(`${API_URL}/triggers/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signals })
  })
}

export function postReset() {
  return apiFetch(`${API_URL}/reset`, { method: 'POST' })
}

export function getModels() {
  return apiFetch(`${API_URL}/models`)
}

// Idempotent: activating the model that's already active still succeeds,
// but the backend skips the session reset entirely — no need for the
// frontend to special-case "already active" before calling this.
export function activateModel(modelName) {
  return apiFetch(`${API_URL}/models/${encodeURIComponent(modelName)}/activate`, {
    method: 'PUT'
  })
}

// Raw request body (not multipart): the model's name is the resource in the
// URL, decided by the caller — never derived server-side from the file.
// Content-Type tells the backend the body's format (zip bundle vs. a lone
// YAML file); it also sniffs the zip magic number as a fallback.
export function putModel(modelName, file) {
  const contentType = /\.zip$/i.test(file.name) ? 'application/zip' : 'application/x-yaml'
  return apiFetch(`${API_URL}/models/${encodeURIComponent(modelName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: file
  })
}

export function deleteModel(modelName) {
  return apiFetch(`${API_URL}/models/${encodeURIComponent(modelName)}`, {
    method: 'DELETE'
  })
}

// The read side of the same resource putModel() writes: the returned blob
// is always a zip, byte-for-byte what putModel() accepts back with no
// transformation. Not JSON on success, hence `parse: 'blob'`.
export function downloadModel(modelName) {
  return apiFetch(`${API_URL}/models/${encodeURIComponent(modelName)}`, {}, { parse: 'blob' })
}
