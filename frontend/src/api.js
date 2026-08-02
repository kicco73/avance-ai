import { setApiError } from './errorStore.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

async function apiFetch(url, options, { parse = 'json' } = {}) {
  let res
  try {
    res = await fetch(url, options)
  } catch (err) {
    if (err.name === 'AbortError') throw err
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

export function getState(signal) {
  return apiFetch(`${API_URL}/state`, { signal })
}

export function getCurrentSession(sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/chat/session${query}`)
}

export function postCreateSession() {
  return apiFetch(`${API_URL}/chat/sessions`, { method: 'POST' })
}

export function getSessions() {
  return apiFetch(`${API_URL}/chat/sessions`)
}

export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/chat/messages?session_id=${encodeURIComponent(sessionId)}`)
}

// The full Signals event log for a session (snapshots + transitions,
// chronological) — for the "Benchmark project" view's timeline.
export function getSessionSignals(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/signals`)
}

// Sets (expectedState given) or clears (null) messageId's expert-
// annotated expected state — the "Benchmark project" view's States tab.
// 409 if messageId isn't an evaluation point, 422 for an unknown state.
export function putMessageExpectedState(messageId, expectedState) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/expected-state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_state: expectedState })
  })
}

// Sets or clears messageId's expert-annotated expected signal values —
// the "Benchmark project" view's Signals tab. `expectedValues` is the
// whole replacement dict (a signal name missing from it is annotation-
// cleared for that signal alone); null/{} clears every signal.
export function putMessageExpectedSignals(messageId, expectedValues) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/expected-signals`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_values: expectedValues })
  })
}

// Clears every expert annotation (expected_state and expected_values
// alike) across sessionId's own Signals rows in one call — the
// "Benchmark project" view's "Unlabel all" action, after its own
// confirmation dialog.
export function deleteSessionAnnotations(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/annotations`, {
    method: 'DELETE'
  })
}

export function createChatSocket() {
  return new WebSocket(WS_URL)
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
  return apiFetch(`${API_URL}/chat/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, session_id: sessionId })
  })
}

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

// `messageId`, when given, computes metrics as of that exact message's
// own timestamp instead of the live/current history — see the
// "Benchmark project" view's point-in-time Inspector.
export function getMetrics(messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/chat/metrics${query}`)
}

// Expert-annotation-vs-actual benchmark metrics (see backend's
// metrics_framework/benchmark_metrics) for the active user+project —
// every annotated session, or (sessionId given) just that one. Same
// {name, ui_label, ui_description, value} shape as getMetrics, plus
// sample_count — the "Benchmark project" view's Performance tab.
export function getBenchmarkMetrics(sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/chat/benchmark-metrics${query}`)
}

export function postAction(actionName, sessionId) {
  return apiFetch(`${API_URL}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName, session_id: sessionId })
  })
}

export function getAiModels() {
  return apiFetch(`${API_URL}/ai/models`)
}

export function postAiModelSelection(index) {
  return apiFetch(`${API_URL}/ai/models/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index })
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

// "Restart from here" (EditProjectView.vue only): deletes every message
// (and its own Signals rows) at or after `timestamp` in `sessionId`, and
// rolls the live automaton state back to whatever it was immediately
// before — see backend ChatService.truncate_session. `timestamp` must be
// one of the UTC-explicit ISO strings the backend itself already handed
// back (see db._utc_iso), never a client-constructed one. Returns the
// fresh active state payload, same shape as postReset's.
export function postTruncateSession(sessionId, timestamp) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamp })
  })
}

export function getProjects() {
  return apiFetch(`${API_URL}/projects`)
}

export function getBackup() {
  return apiFetch(`${API_URL}/backup`, {}, { parse: 'blob' })
}

export function postRestoreBackup(file) {
  return apiFetch(`${API_URL}/backup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file
  })
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

export function getProjectGraph(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/graph`)
}

export function getProjectSignals(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals`)
}

export function getProjectFiles(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files`)
}

// {content, version, total_versions} of fileName's latest version — see
// getProjectFileVersion for a specific past one.
export function getProjectFile(projectName, fileName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`)
}

// Same shape as getProjectFile, for the highest stored version not
// exceeding `version` — used by the Edit-project view's Undo/Redo.
export function getProjectFileVersion(projectName, fileName, version) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/versions/${version}`
  )
}

export function getProjectFileVersions(projectName, fileName) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/versions`
  )
}

export function putProjectFile(projectName, fileName, content) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: content
  })
}

export function deleteProjectFile(projectName, fileName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
}

// Discards a project's older file versions, keeping only each one's
// current/latest — called by the Edit-project view when it closes, so a
// project's undo/redo history never outlives one editing session.
export function deleteProjectVersions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/versions`, {
    method: 'DELETE'
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