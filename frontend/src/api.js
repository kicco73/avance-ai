import { setApiError } from './errorStore.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

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

  if (parse === 'blob') return res.blob()
  if (parse === 'text') return res.text()
  return res.json()
}

// Also used as the initial-boot ping (see App.vue): `signal` lets that
// caller bound each attempt with a timeout, since a plain fetch() never
// times out on its own against a hung connection.
export function getState(signal) {
  return apiFetch(`${API_URL}/state`, { signal })
}

export function getMessages() {
  return apiFetch(`${API_URL}/chat/messages`)
}

// Chat normally runs over a websocket: the backend pushes status updates
// (retrying, done, error) as they happen instead of the client polling for
// them. See chatClient.js for the transport choice + fallback.
export function createChatSocket() {
  return new WebSocket(WS_URL)
}

// Synchronous REST alternative to the websocket, for one chat turn — used
// by chatClient.js once the websocket is confirmed unavailable. No
// intermediate "retrying" notifications: the backend still retries
// server-side, just silently from this transport's point of view.
export function postChatMessage(text) {
  return apiFetch(`${API_URL}/chat/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text })
  })
}

// `audioBlob` goes up as multipart/form-data — no Content-Type header set
// here, so fetch() derives the correct boundary itself from the FormData
// body. Routed through apiFetch like everything else: a transcription
// failure surfaces in the same shared error area as any other REST call.
export function postListenTranscribe(audioBlob) {
  const formData = new FormData()
  formData.append('file', audioBlob, 'recording.webm')
  return apiFetch(`${API_URL}/listen/transcribe`, {
    method: 'POST',
    body: formData
  })
}

export function getSignals() {
  return apiFetch(`${API_URL}/chat/signals`)
}

export function postAction(actionName) {
  return apiFetch(`${API_URL}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  })
}

export function getAutoTracking() {
  return apiFetch(`${API_URL}/chat/autotracking`)
}

export function postAutoTracking(enabled) {
  return apiFetch(`${API_URL}/chat/autotracking`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}

// Not fetched through apiFetch: a missing audio (404, e.g. purged or never
// generated) must be silent, not routed through the shared error store —
// see audio.js's playMessageAudio, which just points a plain <audio>-style
// element at this URL and lets a 404 fail quietly on its own.
export function messageAudioUrl(messageId) {
  return `${API_URL}/chat/messages/${messageId}/audio`
}

export function postTriggersPreview(signals) {
  return apiFetch(`${API_URL}/triggers/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signals })
  })
}

export function postReset() {
  return apiFetch(`${API_URL}/chat/reset`, { method: 'POST' })
}

export function getProjects() {
  return apiFetch(`${API_URL}/projects`)
}

export function activateProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/activate`, {
    method: 'PUT'
  })
}

export function putProject(projectName, file) {
  const contentType = /\.zip$/i.test(file.name) ? 'application/zip' : 'application/x-yaml'
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: file
  })
}

export function getProjectFile(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/index.yml`, {}, { parse: 'text' })
}

export function putProjectFile(projectName, content) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/index.yml`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: content
  })
}

export function deleteProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {
    method: 'DELETE'
  })
}

export function downloadProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {}, { parse: 'blob' })
}
