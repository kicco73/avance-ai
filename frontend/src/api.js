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

// allowDraft: only ever true from EditProjectView.vue's own embedded
// "Test" chat — the one place a session is allowed to exist against a
// revision nobody's published yet (see backend ChatService.get_or_
// create_current_session's own docstring).
export function getCurrentSession(sessionId, allowDraft = false) {
  const params = new URLSearchParams()
  if (sessionId != null) params.set('session_id', sessionId)
  if (allowDraft) params.set('allow_draft', 'true')
  const query = params.toString()
  return apiFetch(`${API_URL}/chat/session${query ? `?${query}` : ''}`)
}

export function postCreateSession(allowDraft = false) {
  const query = allowDraft ? '?allow_draft=true' : ''
  return apiFetch(`${API_URL}/chat/sessions${query}`, { method: 'POST' })
}

export function getSessions(includeImported = false) {
  const query = includeImported ? '?include_imported=true' : ''
  return apiFetch(`${API_URL}/chat/sessions${query}`)
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

export function getSignals() {
  return apiFetch(`${API_URL}/chat/signals`)
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

// Expert-annotation-vs-actual benchmark metrics (see backend's
// metrics_framework/benchmark_metrics) for the active user+project —
// every annotated session, or (sessionId given) just that one. Same
// {name, ui_label, ui_description, value} shape as getMetrics, plus
// sample_count — the "Label sessions" view's Performance tab.
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

export function getProjectGraph(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/graph`)
}

// `stateKey`, when given, scopes each signal's own `relevant` field (see
// InspectorSignalsTab.vue's "show only relevant signals" filter) to that
// state's own outgoing actions (see backend's Automaton.
// triggerable_signal_names) — the Inspector's own currently selected/
// highlighted state, or the state a selected action fires *from*.
// Omitted, every state's triggers combine instead (Automaton.
// all_triggerable_signal_names).
export function getProjectSignals(projectName, stateKey) {
  const query = stateKey != null ? `?state_key=${encodeURIComponent(stateKey)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/signals${query}`)
}

export function getProjectFiles(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/files`)
}

// Raw markdown content of one of backend/src/docs' fixed reference docs
// (see controller.py's own DOC_FILES) — backs each "(?)" documentation
// button (EditProjectView.vue's own, next to Save; the Inspector's
// Metrics/Performance tabs). `name` is one of 'project-specs' /
// 'metrics' / 'benchmark'.
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
// AutomatonYamlEditor.set_init_action_target) — its target is the only
// field it exposes an edit for, via this dedicated endpoint rather than
// putActionField above (which looks the action up inside a real state's
// own `actions:` list, and the init-action isn't in one).
export function putInitActionTarget(projectName, target) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/init-action/target`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: target }) }
  )
}

export function putSignalField(projectName, signalName, field, value) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/signals/${encodeURIComponent(signalName)}/${encodeURIComponent(field)}`,
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