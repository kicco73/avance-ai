import { setApiError } from './errorStore.js'
import { requireLogin } from './authStore.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

async function apiFetch(url, options, { parse = 'json' } = {}) {
  let res
  try {
    // The session cookie is httpOnly and, in dev, often cross-origin
    // (VITE_API_URL pointing at a separate backend port) — without this
    // it simply never gets sent, and every call 401s regardless of login.
    res = await fetch(url, { ...options, credentials: 'include' })
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
    // A 401 means "not logged in" — LoginView.vue takes over the whole
    // screen for that, so it doesn't also need an error banner.
    if (res.status === 401) {
      requireLogin()
    } else {
      setApiError(message, detail)
    }
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
    throw err
  }

  // A 204 has no body — res.json() on an empty response throws, regardless
  // of the requested `parse` mode.
  if (res.status === 204) return null
  if (parse === 'blob') return res.blob()
  if (parse === 'text') return res.text()
  return res.json()
}

export function getState(signal) {
  return apiFetch(`${API_URL}/state`, { signal })
}

// `credential` is the Google Identity Services ID token JWT off the
// "Sign in with Google" callback — see LoginView.vue. The backend sets
// the session cookie itself (Set-Cookie on this response); there's
// nothing in the returned body the caller needs to store.
export function postLogin(provider, credential) {
  return apiFetch(`${API_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, credential })
  })
}

export function postLogout() {
  return apiFetch(`${API_URL}/auth/logout`, { method: 'POST' })
}

export function getMe() {
  return apiFetch(`${API_URL}/auth/me`)
}

export function getAuthProviders() {
  return apiFetch(`${API_URL}/auth/providers`)
}

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

export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/messages`)
}

export function getSessionState(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/state`)
}

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
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
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

export function postImportSession(projectName, file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/import`, {
    method: 'POST',
    body: formData
  })
}

// One session object out of a "Download all" .json export — LabelProjectView.vue's
// own handleImportSession calls this once per session found inside an
// uploaded .json file, same per-item try/catch loop it uses per .txt file.
export function postImportSessionJson(projectName, sessionData) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/import-json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sessionData)
  })
}

// The "Label sessions" view's own "Download all" button — every session
// (native and imported alike) of `projectName`, as one JSON array. A blob
// so the caller can trigger a real file download.
export function getExportSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/export`, {}, { parse: 'blob' })
}

export function getSignals() {
  return apiFetch(`${API_URL}/chat/signals`)
}

// `projectName`'s identifier registry — {identifier: description} per
// namespace (signal, env, system, session, metric, ...) a trigger/env
// expression can reference. Used by TriggerEditor's autocomplete.
export function getIdentifiers(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/identifiers`)
}

// The active project's "environment" memory: {stored, action_set,
// computed}, reported separately so the Env tab knows which section each
// value belongs in. `messageId` restricts to values as of that message.
export function getEnv(messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/chat/env${query}`)
}

// Edits (or adds) one stored env key — always live, there's no editing
// history. Returns the same {stored, computed} shape as getEnv.
export function putEnvValue(key, value) {
  return apiFetch(`${API_URL}/chat/env/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  })
}

export function deleteEnvValue(key) {
  return apiFetch(`${API_URL}/chat/env/${encodeURIComponent(key)}`, {
    method: 'DELETE'
  })
}

// Wipes every stored ("AI" section) env key at once. Returns the same
// {stored, action_set, computed} shape as getEnv.
export function clearEnv() {
  return apiFetch(`${API_URL}/chat/env`, {
    method: 'DELETE'
  })
}

// Wipes every action-set ("ACTION" section) env key at once — a distinct
// endpoint from clearEnv, not a query param on it.
export function clearActionEnv() {
  return apiFetch(`${API_URL}/chat/action-env`, {
    method: 'DELETE'
  })
}

// `messageId`, when given, computes metrics as of that exact message's
// own timestamp instead of the live/current history — see the
// "Label sessions" view's point-in-time Inspector.
export function getMetrics(projectName, messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/metrics${query}`)
}

export function postAction(actionName, sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
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

export function getProjects() {
  return apiFetch(`${API_URL}/projects`)
}

// Settings > Runtime status view's own table — every project's own
// {name, status, paused_reason, revision, published_revision}.
export function getProjectsRuntimeStatus() {
  return apiFetch(`${API_URL}/settings/projects/runtime-status`)
}

export function getUsers() {
  return apiFetch(`${API_URL}/users`)
}

// Manual pause/resume — only valid from 'running'/'manually_paused'
// respectively, enforced backend-side; a 400 means the status shown was
// already stale.
export function putProjectPause(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/pause`, { method: 'PUT' })
}

export function putProjectResume(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/resume`, { method: 'PUT' })
}

// "New project" — same effect server-side as uploading samples/Hello
// world.zip by hand (see putProject), minus picking a name first (the
// backend derives/de-duplicates one on its own).
export function postNewProject() {
  return apiFetch(`${API_URL}/projects`, { method: 'POST' })
}

// Settings > "About Avance..." dialog — {name, version}, version being
// whatever the running backend's own __version__ (main.py) currently is.
export function getAbout() {
  return apiFetch(`${API_URL}/settings/about`)
}

export function getBackup() {
  return apiFetch(`${API_URL}/settings/backup`, {}, { parse: 'blob' })
}

export function postRestoreBackup(file) {
  return apiFetch(`${API_URL}/settings/backup`, {
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

// `sessionId`, when given, pins the graph to the exact revision that
// session ran against, instead of the current draft. The "States" tab
// passes the session under review; EditProjectView omits it.
export function getProjectGraph(projectName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/graph${query}`)
}

// `stateKey`, when given, scopes each signal's `relevant` field to that
// state's outgoing actions; omitted, every state's triggers combine
// instead. `sessionId`: see getProjectGraph above.
export function getProjectSignals(projectName, stateKey, sessionId) {
  const params = new URLSearchParams()
  if (stateKey != null) params.set('state_key', stateKey)
  if (sessionId != null) params.set('session_id', sessionId)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals${query}`)
}

// Declared env-key definitions (name/ui_description/value) of the
// project's top-level `env:` section.
export function getProjectEnvKeys(projectName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys${query}`)
}

// The optional top-level `project:` section (id/ui_label/ui_description).
export function getProjectMetadata(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/project`)
}

export function putProjectField(projectName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/project/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function getProjectFiles(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files`)
}

// Raw markdown content of a fixed reference doc, backing each "(?)" doc
// button. `name` is one of 'project-specs' / 'metrics' / 'benchmark'.
export function getDoc(name) {
  return apiFetch(`${API_URL}/docs/${encodeURIComponent(name)}`)
}

// {content, can_undo, can_redo} of fileName's current content —
// can_undo/can_redo drive the editor's Undo/Redo buttons, scoped to the
// current user.
export function getProjectFile(projectName, fileName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`)
}

export function putProjectFile(projectName, fileName, content) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: content
  })
}

// Image attachments: same PUT route as putProjectFile, but the raw File
// as body with its own Content-Type — the backend validates an image
// save against the request header, unlike a text save.
export function putProjectFileBinary(projectName, fileName, file) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file
  })
}

// Raw bytes of fileName's content — for a plain <img src> or a manual
// fetch needing the text body rather than a JSON envelope. `sessionId`
// omitted resolves the current draft; given, resolves that session's revision.
export function projectFileContentUrl(projectName, fileName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/content${query}`
}

// A pure editor preview, not a save — nothing is persisted. `content` is
// the editor's current text, needed so a later redo/undo can restore it;
// the backend still decides what to restore. Response: {content, can_undo, can_redo}.
export function undoProjectFile(projectName, fileName, content) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/undo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function redoProjectFile(projectName, fileName, content) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/redo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function deleteProjectFile(projectName, fileName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
}

// Clears the current user's undo/redo history for every file in the
// project, so a fresh editing session never inherits a previous trail.
export function clearProjectHistory(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/history`, {
    method: 'DELETE'
  })
}

export function deleteProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {
    method: 'DELETE'
  })
}

// index.yml structural editing — add/edit/delete/reorder states, actions,
// and signals without hand-writing YAML. Each call returns just the
// affected object's own payload, never the whole YAML text.

export function postAddState(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states`, { method: 'POST' })
}

export function postAddSignal(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals`, { method: 'POST' })
}

export function postAddEnvKey(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys`, { method: 'POST' })
}

export function postAddAction(projectName, stateName) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions`,
    { method: 'POST' }
  )
}

export function putStateField(projectName, stateName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putActionField(projectName, stateName, actionName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// The init-action lives outside `states:` in the YAML, so unlike
// putActionField it isn't looked up inside a state's `actions:` list —
// every editable field goes through this dedicated endpoint instead.
export function putInitActionField(projectName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/init-action/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSignalField(projectName, signalName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putEnvKeyField(projectName, envKeyName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys/${encodeURIComponent(envKeyName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// 0-based index the action should end up at, within its own state's
// actions list.
export function putActionOrder(projectName, stateName, actionName, position) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/order`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: position }) }
  )
}

export function deleteState(projectName, stateName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectAction(projectName, stateName, actionName) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}`,
    { method: 'DELETE' }
  )
}

export function deleteProjectSignal(projectName, signalName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectEnvKey(projectName, envKeyName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys/${encodeURIComponent(envKeyName)}`, {
    method: 'DELETE'
  })
}

export function downloadProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {}, { parse: 'blob' })
}

export function getProjectRevision(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/revision`)
}

export function getPublishPreview(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/publish/preview`)
}

export function postPublishProject(projectName, remapTo = null) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remap_to: remapTo })
  })
}

export function postRevertProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/revert`, { method: 'POST' })
}

// The "Auto" tab's own replay launch — sessionId null means the
// whole-project-scope run (every labeled session at once).
export function postBenchmarkRun(projectName, sessionId, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/benchmark-runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, strategy })
  })
}

export function getBenchmarkRun(projectName, runId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/benchmark-runs/${encodeURIComponent(runId)}`)
}

export function getBenchmarkRuns(projectName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/benchmark-runs${query}`)
}

// Every real state key of the project's current draft automaton.
export function getProjectStates(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states`)
}

export function postStateTest(projectName, stateKey, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateKey)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function getStateJob(projectName, jobId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/state-jobs/${encodeURIComponent(jobId)}`)
}