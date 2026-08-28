import { setApiError } from './errorStore.js'
import { requireLogin } from './authStore.js'
import { emitProjectChanged } from './projectChangeEvents.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'
const WS_URL = import.meta.env.VITE_WS_URL ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

// Reads a `text/event-stream` body of `data: {...}\n\n` chunks, calling
// `onProgress` for each one, until a `completed`/`failed` chunk arrives —
// used by postImportSessions to show real progress instead of a spinner.
async function readSseResult(res, onProgress) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let final = null
  while (!final) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      if (!chunk.startsWith('data: ')) continue
      const message = JSON.parse(chunk.slice('data: '.length))
      onProgress?.(message)
      if (message.queue_status === 'exited') final = message
    }
  }
  if (final?.job_status === 'failed') {
    const message = final.error || 'Import failed.'
    setApiError(message, '')
    throw new Error(message)
  }
  return final?.result ?? null
}

async function apiFetch(url, options, { parse = 'json', onProgress } = {}) {
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
  if (parse === 'sse') return readSseResult(res, onProgress)
  if (parse === 'response') return res
  return res.json()
}

async function projectFetch(projectName, url, options, fetchOpts) {
  const result = await apiFetch(url, options, fetchOpts)
  await emitProjectChanged(projectName)
  return result
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

// Raw markdown content of the Terms of Service — reachable even by a
// pending (not-yet-registered) or fully anonymous session, since
// TermsView.vue must show it before there's any account to gate on.
export function getTerms() {
  return apiFetch(`${API_URL}/auth/terms`)
}

// TermsView.vue's Accept action — creates the User row postLogin's own
// login() deliberately deferred (see auth_service.py).
export function postAcceptTerms() {
  return apiFetch(`${API_URL}/auth/accept-terms`, { method: 'POST' })
}

export function getLegalTermsStatus(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/legal-terms-status`)
}

export function postAcceptProjectTerms(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/accept-terms`, { method: 'POST' })
}

// ProfileView.vue's "Erase all my data" — deletes the account and
// everything tied to it server-side (see Db.erase_user_data); also
// clears the session cookie itself.
export function postEraseData() {
  return apiFetch(`${API_URL}/auth/erase-data`, { method: 'POST' })
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

// The "Label sessions" view's own Import button — every selected file in
// one request, whichever mix of a .txt transcript and a "Download all"
// .json export it contains. All per-file/per-session dispatch and error
// handling happens server-side. Streams SSE progress chunks within this
// same request/response (see post_import_sessions); pass `onProgress
// (message)` to render live percentage instead of a spinner. The
// returned promise resolves with the final {results, last_session_id}.
export function postImportSessions(projectName, files, onProgress) {
  const formData = new FormData()
  for (const file of files) formData.append('files', file)
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/import`, {
    method: 'POST',
    body: formData
  }, { parse: 'sse', onProgress })
}

// The "Label sessions" view's own "Download all" button — every session
// of `projectName` matching `type` ('live' | 'imported'), as one JSON
// array. A blob so the caller can trigger a real file download.
export function getExportSessions(projectName, type) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/export?type=${encodeURIComponent(type)}`,
    {}, { parse: 'blob' }
  )
}

// The "Label sessions" view's own "Delete all imported sessions" button —
// every imported session of `projectName`, across every user.
export function deleteImportedSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/imported`, {
    method: 'DELETE'
  })
}

// SessionsTree.vue's own drag-and-drop between branches — `username` is
// whichever branch the sessions were dropped on, a "Test user N" one or
// any other imported username alike.
export function putSessionsReassign(projectName, sessionIds, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/reassign`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_ids: sessionIds, username })
  })
}

export function deleteTestUser(projectName, testUserSeq) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-users/${encodeURIComponent(testUserSeq)}`, {
    method: 'DELETE'
  })
}

// The "Label sessions" view's per-branch × button for any non-live
// branch that isn't a "Test user N" one — an arbitrary imported username.
export function deleteUserSessions(projectName, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/users/${encodeURIComponent(username)}`, {
    method: 'DELETE'
  })
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
// "Label sessions" view's point-in-time Inspector. `full`: every core
// metric, including ones that need more than one session (e.g.
// Retention/Activity Consistency) instead of the usual "one_session"
// subset. `username`, when given, computes metrics for that user's own
// sessions instead of the caller's — see Manage Users' own statistics panel.
export function getMetrics(projectName, messageId, full, username) {
  const params = new URLSearchParams()
  if (messageId != null) params.set('message_id', messageId)
  if (full) params.set('full', 'true')
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/metrics${query}`)
}

export function getUserLatestSignals(projectName, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/${encodeURIComponent(username)}/latest-signals`)
}

export function getTimeline(projectName, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/${encodeURIComponent(username)}/timeline`)
}

export function getMetricsHistory(projectName, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/${encodeURIComponent(username)}/metrics-history`)
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

export function getTestChatModels() {
  return apiFetch(`${API_URL}/ai/models/test`)
}

export function postTestChatModelSelection(index) {
  return apiFetch(`${API_URL}/ai/models/test/selection`, {
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

export function putUserRole(userId, role) {
  return apiFetch(`${API_URL}/users/${encodeURIComponent(userId)}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role })
  })
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

// Streams progress SSE-style within this same response, same as
// postImportSessions — see readSseResult. `onProgress` gets each chunk's
// `percentage` (0-100) as the queued import of any bundled
// sessions.json/tests.json advances.
export function putProject(projectName, file, onProgress) {
  const contentType = /\.zip$/i.test(file.name) ? 'application/zip' : 'application/x-yaml'
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: file
  }, { parse: 'sse', onProgress })
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

// { tokens: number | null } — estimated input-token cost of `stateKey`'s
// own turn prompt (attachments, signal/reaction definitions, env, ...),
// null when no AiService is configured. `sessionId`: see getProjectGraph above.
export function getStateInputTokens(projectName, stateKey, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateKey)}/tokens${query}`)
}

export function putProjectField(projectName, field, value) {
  return projectFetch(
    projectName,
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
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: content
  })
}

// Image attachments: same PUT route as putProjectFile, but the raw File
// as body with its own Content-Type — the backend validates an image
// save against the request header, unlike a text save.
export function putProjectFileBinary(projectName, fileName, file) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
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
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
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

export function postWipeLiveSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/live-sessions/wipe`, {
    method: 'POST'
  })
}

// index.yml structural editing — add/edit/delete/reorder states, actions,
// and signals without hand-writing YAML. Each call returns just the
// affected object's own payload, never the whole YAML text.

export function postAddState(projectName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/states`, { method: 'POST' })
}

export function postAddSignal(projectName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/signals`, { method: 'POST' })
}

export function postAddEnvKey(projectName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys`, { method: 'POST' })
}

export function postAddAction(projectName, stateName) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions`,
    { method: 'POST' }
  )
}

export function putStateField(projectName, stateName, field, value) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putActionField(projectName, stateName, actionName, field, value) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// The init-action lives outside `states:` in the YAML, so unlike
// putActionField it isn't looked up inside a state's `actions:` list —
// every editable field goes through this dedicated endpoint instead.
export function putInitActionField(projectName, field, value) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/init-action/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSignalField(projectName, signalName, field, value) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putEnvKeyField(projectName, envKeyName, field, value) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys/${encodeURIComponent(envKeyName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// 0-based index the action should end up at, within its own state's
// actions list.
export function putActionOrder(projectName, stateName, actionName, position) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/order`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: position }) }
  )
}

export function deleteState(projectName, stateName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectAction(projectName, stateName, actionName) {
  return projectFetch(
    projectName,
    `${API_URL}/projects/${encodeURIComponent(projectName)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}`,
    { method: 'DELETE' }
  )
}

export function deleteProjectSignal(projectName, signalName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectEnvKey(projectName, envKeyName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys/${encodeURIComponent(envKeyName)}`, {
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
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/revert`, { method: 'POST' })
}

// The "Auto" tab's own replay launch — sessionId null means the
// whole-project-scope run (every labeled session at once). `username`,
// when given, scopes that whole-project run to just that user's sessions
// instead of the requesting user's own.
export function postTest(projectName, sessionId, strategy, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, strategy, ...(username != null ? { username } : {}) })
  })
}

export function getTest(projectName, testId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests/${encodeURIComponent(testId)}`)
}

export function getTests(projectName, sessionId, username) {
  const params = new URLSearchParams()
  if (sessionId != null) params.set('session_id', sessionId)
  if (username != null) params.set('username', username)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests${query}`)
}

export function deleteTests(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests`, { method: 'DELETE' })
}

export function getTestMetrics(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/tests/metrics`)
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

export function getJobsStatus(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/jobs-status?strategy=${encodeURIComponent(strategy)}`)
}

export function getAggregateResult(projectName, kind, target, strategy) {
  const params = new URLSearchParams({ kind, strategy })
  if (target != null) params.set('target', target)
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/aggregate-result?${params}`)
}

export function postStatesAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/states/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalsAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postRootAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/root/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUsersAggregation(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/aggregation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSessionsRun(projectName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postUserSessionsRun(projectName, username, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/users/${encodeURIComponent(username)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}

export function postSignalTest(projectName, signalName, strategy) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  })
}