const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

async function throwForResponse(res) {
  let detail = `Error ${res.status}`
  try {
    const body = await res.json()
    if (body.detail) detail = body.detail
  } catch {
    // ignore non-JSON body
  }
  const err = new Error(detail)
  err.status = res.status
  throw err
}

async function handleResponse(res) {
  if (!res.ok) return throwForResponse(res)
  return res.json()
}

// Also used as the initial-boot ping (see App.vue): `signal` lets that
// caller bound each attempt with a timeout, since a plain fetch() never
// times out on its own against a hung connection.
export function getState(signal) {
  return fetch(`${API_URL}/state`, { signal }).then(handleResponse)
}

export function getMessages() {
  return fetch(`${API_URL}/messages`).then(handleResponse)
}

// Chat runs over a websocket: the backend pushes status updates (retrying,
// done, failed) as they happen instead of the client polling for them.
export function createChatSocket() {
  return new WebSocket(WS_URL)
}

export function getSignals() {
  return fetch(`${API_URL}/signals`).then(handleResponse)
}

export function postAction(actionName) {
  return fetch(`${API_URL}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  }).then(handleResponse)
}

export function getAutoTracking() {
  return fetch(`${API_URL}/autotracking`).then(handleResponse)
}

export function postAutoTracking(enabled) {
  return fetch(`${API_URL}/autotracking`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  }).then(handleResponse)
}

export function postTriggersPreview(signals) {
  return fetch(`${API_URL}/triggers/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signals })
  }).then(handleResponse)
}

export function postReset() {
  return fetch(`${API_URL}/reset`, { method: 'POST' }).then(handleResponse)
}

export function getModels() {
  return fetch(`${API_URL}/models`).then(handleResponse)
}

// Idempotent: activating the model that's already active still succeeds,
// but the backend skips the session reset entirely — no need for the
// frontend to special-case "already active" before calling this.
export function activateModel(modelName) {
  return fetch(`${API_URL}/models/${encodeURIComponent(modelName)}/activate`, {
    method: 'PUT'
  }).then(handleResponse)
}

// Raw request body (not multipart): the model's name is the resource in the
// URL, decided by the caller — never derived server-side from the file.
// Content-Type tells the backend the body's format (zip bundle vs. a lone
// YAML file); it also sniffs the zip magic number as a fallback.
export function putModel(modelName, file) {
  const contentType = /\.zip$/i.test(file.name) ? 'application/zip' : 'application/x-yaml'
  return fetch(`${API_URL}/models/${encodeURIComponent(modelName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: file
  }).then(handleResponse)
}

// Unlike activate/put, failures here (unknown model, attempt to delete
// "default") surface as a non-2xx status with {detail}, which handleResponse
// turns into a thrown Error — there's no {success: false, error} shape to
// check on the resolved value.
export function deleteModel(modelName) {
  return fetch(`${API_URL}/models/${encodeURIComponent(modelName)}`, {
    method: 'DELETE'
  }).then(handleResponse)
}

// The read side of the same resource putModel() writes: the returned blob
// is always a zip, byte-for-byte what putModel() accepts back with no
// transformation. Not JSON on success, so this can't go through
// handleResponse (which assumes a JSON body) — only its error path is
// reused, via throwForResponse.
export async function downloadModel(modelName) {
  const res = await fetch(`${API_URL}/models/${encodeURIComponent(modelName)}`)
  if (!res.ok) return throwForResponse(res)
  return res.blob()
}
