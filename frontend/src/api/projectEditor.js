import { apiFetch, projectFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

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

// ShareProjectDialog.vue's own trigger — a fresh Invite row every time
// the dialog opens (see backend's InviteManager.create_invite), never
// reused. { code, expires_at, max_shares }.
export function postCreateInvite(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/invites`, { method: 'POST' })
}

// Resolves a "share project" invite code back to the project it was
// generated for — { project_name: string | null }. Used by
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

// Renames one file in place — the new basename only, same folder as
// fileName (see ProjectEditor.rename_project_file, which also
// auto-rewrites any index.yml/index.css reference to the old basename).
// Response: {old_name, content, can_undo, can_redo, ...} for newName.
export function renameProjectFile(projectName, fileName, newName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${encodeURIComponent(fileName)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName })
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

// A pure editor preview, same shape as undo/redo above — nothing is
// persisted. Response: {content} — the new file text for the caller to
// drop into its own (unsaved) editor buffer.
function aiEditProjectFile(projectName, fileName, instruction) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/files/${fileName}/ai-edit`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruction }) }
  )
}

export function aiEditIndexYml(projectName, instruction) {
  return aiEditProjectFile(projectName, 'index.yml', instruction)
}

export function aiEditIndexCss(projectName, instruction) {
  return aiEditProjectFile(projectName, 'index.css', instruction)
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

export function postAddLegalTerms(projectName) {
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}/legal-terms`, { method: 'POST' })
}

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
