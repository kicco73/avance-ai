import { apiFetch, projectFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// `sessionId`, when given, pins the graph to the exact revision that
// session ran against, instead of the current draft. The "States" tab
// passes the session under review; EditProjectView omits it.
export function getProjectGraph(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/graph${query}`)
}

// `stateKey`, when given, scopes each signal's `relevant` field to that
// state's outgoing actions; omitted, every state's triggers combine
// instead. `sessionId`: see getProjectGraph above.
export function getProjectSignals(projectId, stateKey, sessionId) {
  const params = new URLSearchParams()
  if (stateKey != null) params.set('state_key', stateKey)
  if (sessionId != null) params.set('session_id', sessionId)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/signals${query}`)
}

// Declared env-key definitions (name/ui_description/value) of the
// project's top-level `env:` section.
export function getProjectEnvKeys(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/env-keys${query}`)
}

// The optional top-level `project:` section (id/ui_label/ui_description).
export function getProjectMetadata(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/project`)
}

// Declared source definitions (name/ui_label/ui_description/url) of the
// project's top-level `sources:` section.
export function getProjectSources(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/sources${query}`)
}

// ShareProjectDialog.vue's own trigger — a fresh Invite row every time
// the dialog opens (see backend's InviteManager.create_invite), never
// reused. { code, expires_at, max_shares }.
export function postCreateInvite(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/invites`, { method: 'POST' })
}

// Resolves a "share project" invite code back to the project it was
// generated for — { project_id: string | null }. Used by
// useAppBoot.js to land a scanned invite link (shareLink.js) on the
// right project. A POST, not a GET: for a plain 'user' reaching this
// project for the first time, it also consumes the invite and grants
// them access (creates a UserProject row) server-side.
export function postRedeemInviteCode(code) {
  return apiFetch(`${API_URL}/projects/by-invite/${encodeURIComponent(code)}`, { method: 'POST' })
}

// { tokens: number | null } — estimated input-token cost of `stateKey`'s
// own turn prompt (attachments, signal/reaction definitions, env, ...),
// null when no AiService is configured. `sessionId`: see getProjectGraph above.
export function getStateInputTokens(projectId, stateKey, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateKey)}/tokens${query}`)
}

export function putProjectField(projectId, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/project/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function getProjectFiles(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/files`)
}

// Raw markdown content of a fixed reference doc, backing each "(?)" doc
// button. `name` is one of 'project-specs' / 'metrics' / 'benchmark'.
export function getDoc(name) {
  return apiFetch(`${API_URL}/docs/${encodeURIComponent(name)}`)
}

// {content, can_undo, can_redo} of fileName's current content —
// can_undo/can_redo drive the editor's Undo/Redo buttons, scoped to the
// current user.
export function getProjectFile(projectId, fileName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`)
}

export function putProjectFile(projectId, fileName, content) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: content
  })
}

// Renames one file in place — the new basename only, same folder as
// fileName (see ProjectEditor.rename_project_file, which also
// auto-rewrites any index.yml/index.css reference to the old basename).
// Response: {old_name, content, can_undo, can_redo, ...} for newName.
export function renameProjectFile(projectId, fileName, newName) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName })
  })
}

// Image attachments: same PUT route as putProjectFile, but the raw File
// as body with its own Content-Type — the backend validates an image
// save against the request header, unlike a text save.
export function putProjectFileBinary(projectId, fileName, file) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file
  })
}

// Raw bytes of fileName's content — for a plain <img src> or a manual
// fetch needing the text body rather than a JSON envelope. `sessionId`
// omitted resolves the current draft; given, resolves that session's revision.
export function projectFileContentUrl(projectId, fileName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/content${query}`
}

// A pure editor preview, not a save — nothing is persisted. `content` is
// the editor's current text, needed so a later redo/undo can restore it;
// the backend still decides what to restore. Response: {content, can_undo, can_redo}.
export function undoProjectFile(projectId, fileName, content) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/undo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function redoProjectFile(projectId, fileName, content) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/redo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

// A pure editor preview, same shape as undo/redo above — nothing is
// persisted. Response: {content} — the new file text for the caller to
// drop into its own (unsaved) editor buffer.
function aiEditProjectFile(projectId, fileName, instruction) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${fileName}/ai-edit`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruction }) }
  )
}

export function aiEditIndexYml(projectId, instruction) {
  return aiEditProjectFile(projectId, 'index.yml', instruction)
}

export function aiEditIndexCss(projectId, instruction) {
  return aiEditProjectFile(projectId, 'index.css', instruction)
}

export function deleteProjectFile(projectId, fileName) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
}

// Clears the current user's undo/redo history for every file in the
// project, so a fresh editing session never inherits a previous trail.
export function clearProjectHistory(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/history`, {
    method: 'DELETE'
  })
}

export function postAddLegalTerms(projectId) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/legal-terms`, { method: 'POST' })
}

export function postAddState(projectId) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/states`, { method: 'POST' })
}

export function postAddSignal(projectId) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/signals`, { method: 'POST' })
}

export function postAddEnvKey(projectId) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/env-keys`, { method: 'POST' })
}

export function postAddSource(projectId) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/sources`, { method: 'POST' })
}

export function postAddAction(projectId, stateName) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions`,
    { method: 'POST' }
  )
}

export function putStateField(projectId, stateName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putActionField(projectId, stateName, actionName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// The init-action lives outside `states:` in the YAML, so unlike
// putActionField it isn't looked up inside a state's `actions:` list —
// every editable field goes through this dedicated endpoint instead.
export function putInitActionField(projectId, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/init-action/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSignalField(projectId, signalName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putEnvKeyField(projectId, envKeyName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/env-keys/${encodeURIComponent(envKeyName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSourceField(projectId, sourceName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// 0-based index the action should end up at, within its own state's
// actions list.
export function putActionOrder(projectId, stateName, actionName, position) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/order`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: position }) }
  )
}

export function deleteState(projectId, stateName) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectAction(projectId, stateName, actionName) {
  return projectFetch(
    projectId,
    `${API_URL}/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}`,
    { method: 'DELETE' }
  )
}

export function deleteProjectSignal(projectId, signalName) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectEnvKey(projectId, envKeyName) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/env-keys/${encodeURIComponent(envKeyName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectSource(projectId, sourceName) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}`, {
    method: 'DELETE'
  })
}

export function getProjectRevision(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/revision`)
}

export function getPublishPreview(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/publish/preview`)
}

export function postPublishProject(projectId, remapTo = null) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remap_to: remapTo })
  })
}

export function postRevertProject(projectId) {
  return projectFetch(projectId, `${API_URL}/projects/${encodeURIComponent(projectId)}/revert`, { method: 'POST' })
}
