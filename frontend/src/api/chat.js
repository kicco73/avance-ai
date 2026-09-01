import { apiFetch } from './core.js'
import { setApiError } from '../errorStore.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

export function getCurrentSession(sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/chat/session${query}`)
}

export function postCreateSession() {
  return apiFetch(`${API_URL}/chat/sessions`, { method: 'POST' })
}

// EditProjectView's embedded "Test" chat — the one place a session can
// exist against an unpublished revision. Which revision applies is
// decided by which endpoint is called, never by a caller-supplied flag.
export function getCurrentTestSession(sessionId, projectName) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions/current${query}`)
}

export function postCreateTestSession(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions`, { method: 'POST' })
}

export function getSessions(projectName, includeImported = false) {
  const query = includeImported ? '?include_imported=true' : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions${query}`)
}

// EditProjectView's embedded "Test" chat's own Sessions panel — a
// separate list from getSessions: a "Test" session never appears there,
// and a real one never appears here.
export function getTestSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions`)
}

export function postResetTestSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions/reset`, { method: 'POST' })
}

export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/messages`)
}

export function getSessionState(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/state`)
}

export function createChatSocket() {
  return new WebSocket(WS_URL)
}

export function createTestEventsSource(projectName) {
  return new EventSource(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/test-events`, { withCredentials: true }
  )
}

export function sendWebSocketMessage(payload, { onChunk, onStatus, onDone, onError } = {}) {
  return new Promise((resolve, reject) => {
    let ws
    try {
      ws = createChatSocket()
    } catch (err) {
      if (onError) onError(err)
      reject(err)
      return
    }

    ws.onopen = () => {
      const messageData = typeof payload === 'string' ? { message: payload } : payload
      ws.send(JSON.stringify(messageData))
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        if (data.type === 'status' || data.status) {
          if (onStatus) onStatus(data.status || data.message)
        }
        if (data.type === 'chunk' || data.chunk || data.delta || data.content) {
          const chunkText = data.chunk ?? data.delta ?? data.content ?? ''
          if (onChunk) onChunk(chunkText)
        }
        if (data.type === 'done' || data.done || data.finished) {
          if (onDone) onDone(data)
          ws.close()
          resolve(data)
        }
        if (data.type === 'error' || data.error) {
          const errMsg = data.error?.message || data.error || 'WebSocket Streaming Error'
          setApiError(errMsg, data.error?.detail || '')
          const err = new Error(errMsg)
          if (onError) onError(err)
          ws.close()
          reject(err)
        }
      } catch (e) {
        if (onChunk) onChunk(event.data)
      }
    }

    ws.onerror = (evt) => {
      const err = new Error('WebSocket connection error')
      setApiError('WebSocket connection error', '')
      if (onError) onError(err)
      reject(err)
    }

    ws.onclose = () => {
      
    }
  })
}

export function postChatMessage(text, sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text })
  }, { parse: 'response' })
}

export function postListenTranscribe(audioBlob) {
  const formData = new FormData()
  formData.append('file', audioBlob, 'recording.webm')
  return apiFetch(`${API_URL}/listen/transcribe`, {
    method: 'POST',
    body: formData
  })
}

export function postAction(actionName, sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  })
}

// "Dev mode: freeze automatic state transitions" — EditProjectView's
// embedded "Test" chat only, per test session, never global.
export function getAutoTracking(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/autotracking`)
}

export function postAutoTracking(sessionId, enabled) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/autotracking`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}

export function getActuators(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/actuators`)
}

export function postActuators(sessionId, enabled) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/actuators`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  })
}

export function messageAudioUrl(messageId) {
  return `${API_URL}/chat/messages/${messageId}/audio`
}

// "Restart from here": deletes every message (and its Signals rows) at
// or after `timestamp` in `sessionId`, rolling state back to what it was
// immediately before. `timestamp` must be a backend-issued ISO string.
export function postTruncateSession(sessionId, timestamp) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
  })
}
