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

  // A 204 (see e.g. deleteState/deleteAction/deleteSignal below) never
  // has a body at all — res.json() on an empty one throws a parse error,
  // regardless of which `parse` mode the caller asked for.
  if (res.status === 204) return null
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

// EditProjectView.vue's own embedded "Test" chat — the one place a
// session is allowed to exist against a revision nobody's published yet
// (see backend ChatService.create_draft_session/get_or_create_current_
// draft_session's own docstrings). Which revision a session may exist
// against is decided solely by which endpoint is called now, never by a
// caller-supplied flag on the two above.
export function getCurrentTestSession(sessionId, projectName) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions/current${query}`)
}

export function postCreateTestSession(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions`, { method: 'POST' })
}

export function getSessions(includeImported = false) {
  const query = includeImported ? '?include_imported=true' : ''
  return apiFetch(`${API_URL}/chat/sessions${query}`)
}

// EditProjectView.vue's own embedded "Test" chat's own Sessions panel —
// the draft-session equivalent of getSessions, a fully separate list
// (see backend ChatService.list_test_sessions's own docstring): a "Test"
// session never appears in getSessions, and a real one never appears here.
export function getTestSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-sessions`)
}

export function deleteSession(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export function getMessages(sessionId) {
  return apiFetch(`${API_URL}/chat/messages?session_id=${encodeURIComponent(sessionId)}`)
}

// The full Signals event log for a session (snapshots + transitions,
// chronological) — for the "Label sessions" view's timeline.
export function getSessionSignals(sessionId) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/signals`)
}

// Sets (expectedState given) or clears (null) messageId's expert-
// annotated expected state — the "Label sessions" view's States tab.
// 409 if messageId isn't an evaluation point, 422 for an unknown state.
export function putMessageExpectedState(messageId, expectedState) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/expected-state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_state: expectedState })
  })
}

// Sets or clears messageId's expert-annotated expected signal values —
// the "Label sessions" view's Signals tab. `expectedValues` is the
// whole replacement dict (a signal name missing from it is annotation-
// cleared for that signal alone); null/{} clears every signal.
export function putMessageExpectedSignals(messageId, expectedValues) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/expected-signals`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_values: expectedValues })
  })
}

// Sets or clears messageId's expert-left free-text comment — the
// "Label sessions" view's per-message comment bubble. Unlike
// putMessageExpectedState/putMessageExpectedSignals, every message is a
// legitimate target (no 409 for "not an evaluation point").
export function putMessageComment(messageId, comment) {
  return apiFetch(`${API_URL}/chat/messages/${encodeURIComponent(messageId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}

// Sets (or clears) a session's own persisted "reviewed by a domain
// expert" flag — the "Label sessions" view's own "Mark done" button
// (see backend ChatSession.labeled/ChatService.mark_session_labeled),
// the source of truth for that session's own has_annotations marker
// from here on, replacing the old any-annotation heuristic. A toggle:
// the same call with `false` un-marks it again.
export function putSessionLabeled(sessionId, labeled) {
  return apiFetch(`${API_URL}/chat/sessions/${encodeURIComponent(sessionId)}/labeled`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ labeled })
  })
}

// Renames a session — the "Label sessions" view's own Info tab. null (or
// blank) clears it back to unset. Returns the same session payload
// putSessionLabeled does, so the frontend can refresh its own Sessions
// panel row from the response directly.
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
// alike) across sessionId's own Signals rows in one call — the
// "Label sessions" view's "Unlabel all" action, after its own
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

export function postImportSession(file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiFetch(`${API_URL}/chat/sessions/import`, {
    method: 'POST',
    body: formData
  })
}

// One session object out of a "Download all" .json export (see backend
// tracking/session_export.py's own module docstring for the shape) —
// LabelProjectView.vue's own handleImportSession calls this once per
// session found inside an uploaded .json file, same per-item try/catch
// loop it already runs per .txt file.
export function postImportSessionJson(sessionData) {
  return apiFetch(`${API_URL}/chat/sessions/import-json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sessionData)
  })
}

// The "Label sessions" view's own "Download all" button — every session
// (native and imported alike) of the active project, as one JSON array
// (see backend tracking/session_export.py). A blob so the caller can
// trigger a real file download the same way downloadProject already does
// for a project's own zip.
export function getExportSessions() {
  return apiFetch(`${API_URL}/chat/sessions/export`, {}, { parse: 'blob' })
}

export function getSignals() {
  return apiFetch(`${API_URL}/chat/signals`)
}

// The active project's own identifier registry (see backend's automaton.
// identifier_registry.build_registry) — {identifier: description} per
// namespace (signal, env, system, session, "session.metric", metric) a
// trigger/env: expression can reference. InspectorDetailCard.vue's own
// trigger editor (see TriggerEditor.vue) is the one consumer, for its own
// autocomplete/syntax coloring.
export function getIdentifiers() {
  return apiFetch(`${API_URL}/chat/identifiers`)
}

// The active user+project's current "environment" memory (see backend's
// chat.env.Env) — {stored, action_set, computed}: `stored` key:values the
// model has reported via [env]...[/env] (or a person has edited directly,
// see putEnvValue/deleteEnvValue below), `action_set` ones an action's own
// YAML `env:` field set (see automaton_builder.py's _build_action — never
// editable/deletable through this API, only ever a side effect of an
// action firing), and every always-computed key — reported separately so
// InspectorEnvTab.vue knows which section ("AI"/"SET"/"COMPUTED") each
// value belongs in. `messageId`, when given, restricts to values as they
// stood at or before that exact message (same convention as getMetrics).
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

// Wipes every stored ("AI" section) env key at once — always live.
// Returns the same {stored, action_set, computed} shape as getEnv.
export function clearEnv() {
  return apiFetch(`${API_URL}/chat/env`, {
    method: 'DELETE'
  })
}

// Wipes every action-set ("ACTION" section) env key at once — always
// live. A distinct top-level path, not /chat/env/action — see
// controller.py's own clear_action_env for why. Returns the same
// {stored, action_set, computed} shape as getEnv.
export function clearActionEnv() {
  return apiFetch(`${API_URL}/chat/action-env`, {
    method: 'DELETE'
  })
}

// `messageId`, when given, computes metrics as of that exact message's
// own timestamp instead of the live/current history — see the
// "Label sessions" view's point-in-time Inspector.
export function getMetrics(messageId) {
  const query = messageId != null ? `?message_id=${encodeURIComponent(messageId)}` : ''
  return apiFetch(`${API_URL}/chat/metrics${query}`)
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

// Settings > Runtime status view's own table (see ProjectService.
// get_runtime_status) — every project's own {name, status, paused_reason,
// revision, published_revision}, status one of 'running'/'paused'/
// 'manually_paused'.
export function getProjectsRuntimeStatus() {
  return apiFetch(`${API_URL}/projects/runtime-status`)
}

// Manual pause/resume (see ProjectService.set_manually_paused/
// set_manually_running) — only ever valid from 'running'/'manually_paused'
// respectively, enforced backend-side; a 400 here means the status shown
// was already stale (someone/something else changed it first).
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
  return apiFetch(`${API_URL}/projects/new`, { method: 'POST' })
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

// `sessionId`, when given, pins the graph to the exact revision that
// session's own automaton ran against instead of the current draft — see
// backend ProjectService._resolve_inspector_revision. LabelProjectView.vue's
// own "States" tab passes the session currently being reviewed; every
// other caller (EditProjectView.vue) omits it and keeps reading the draft.
export function getProjectGraph(projectName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/graph${query}`)
}

// `stateKey`, when given, scopes each signal's own `relevant` field (see
// InspectorSignalsTab.vue's "show only relevant signals" filter) to that
// state's own outgoing actions (see backend's Automaton.
// triggerable_signal_names) — the Inspector's own currently selected/
// highlighted state, or the state a selected action fires *from*.
// Omitted, every state's triggers combine instead (Automaton.
// all_triggerable_signal_names). `sessionId`: see getProjectGraph above.
export function getProjectSignals(projectName, stateKey, sessionId) {
  const params = new URLSearchParams()
  if (stateKey != null) params.set('state_key', stateKey)
  if (sessionId != null) params.set('session_id', sessionId)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals${query}`)
}

// Declared env-key definitions (name/ui_description/value) of the
// project's own top-level `env:` section — see InspectorEnvKeysTab.vue's
// own Inspector tab, EditProjectView.vue's "Edit project" schema view.
export function getProjectEnvKeys(projectName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/env-keys${query}`)
}

// The optional top-level `project:` section (id/ui_label/ui_description) —
// see InspectorProjectCard.vue's own Info-tab usage, EditProjectView.vue's
// "Edit project" schema view.
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

// Raw markdown content of one of backend/src/docs' fixed reference docs
// (see controller.py's own DOC_FILES) — backs each "(?)" documentation
// button (EditProjectView.vue's own, next to Save; the Inspector's
// Metrics tab). `name` is one of 'project-specs' / 'metrics' / 'benchmark'.
export function getDoc(name) {
  return apiFetch(`${API_URL}/docs/${encodeURIComponent(name)}`)
}

// {content, can_undo, can_redo} of fileName's current content —
// can_undo/can_redo are what the Edit-project view's Undo/Redo buttons
// use to know whether they're enabled, scoped to the current user.
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

// Image attachments (see project_service.py's IMAGE_EXTENSIONS) — same
// PUT .../files/{file_name} route as putProjectFile above, but the raw
// File itself as the body with its own browser-reported type as
// Content-Type (mirrors putProject's own zip/yaml upload, api.js:407-414
// above), since the backend validates an image save against the request's
// actual Content-Type header rather than inferring it (only a text save's
// content_type is inferred from the extension — see put_project_file's
// own docstring).
export function putProjectFileBinary(projectName, fileName, file) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file
  })
}

// Raw bytes of fileName's own content — for a plain <img src> (the file
// explorer's own image preview, EditProjectView.vue) or a manual fetch
// (ChatWindow.vue's own index.css skin loading, which needs the text body
// rather than a JSON envelope). `sessionId` omitted resolves against the
// current draft; given, resolves the same revision that session's own
// automaton runs against (see controller.py's own get_project_file_content
// docstring for the live/'test' distinction) — never encoded here, just
// passed through as a query param exactly as given.
export function projectFileContentUrl(projectName, fileName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/content${query}`
}

// A pure editor preview, not a save: nothing is persisted, the active
// project/conversation is never reloaded. `content` is whatever the
// editor currently shows, needed so a later redo/undo can bring it back
// — the backend still decides which past/future content to restore, the
// frontend never navigates by version. Response is {content, can_undo,
// can_redo}.
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

// Clears the current user's own undo/redo history for every file in the
// project — called by the Edit-project view when it opens, so a fresh
// editing session never inherits a previous one's undo/redo trail.
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

// index.yml structural editing — add/edit/delete/reorder states,
// actions, and signals without hand-writing YAML (see backend's
// AutomatonYamlEditor). Every one of these returns just the affected
// object's own payload (StatePayload/ActionPayload/SignalPayload), never
// the whole YAML text — see controller.py's own docstring for the whole
// family.

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

// The init-action lives outside `states:` in the YAML (see
// AutomatonYamlEditor.set_init_action_field) — putActionField above looks
// an action up inside a real state's own `actions:` list, and the
// init-action isn't in one, so every one of its own editable fields
// (target, ui-label, ...) goes through this dedicated endpoint instead.
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

// The "Auto" tab's own replay launch (see ProjectAutoPanel.vue) — sessionId
// null means the whole-project-scope run (every labeled session at once),
// same convention as everywhere else this system uses session_id.
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

// Every real state key of the project's current draft automaton — the
// "Stati" branch's own node list (see TestsTree.vue).
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