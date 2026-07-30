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

export function getMessages() {
  return apiFetch(`${API_URL}/chat/messages`)
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

export function postChatMessage(text) {
  return apiFetch(`${API_URL}/chat/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text })
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

export function getProjectGraph(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/graph`)
}

export function getProjectSignals(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals`)
}

export function getProjectFiles(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files`)
}

export function getProjectFile(projectName, fileName) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`,
    {},
    { parse: 'text' }
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

export function deleteProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {
    method: 'DELETE'
  })
}

export function downloadProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {}, { parse: 'blob' })
}