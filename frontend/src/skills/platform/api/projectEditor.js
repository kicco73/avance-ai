import { apiFetch, projectFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// `sessionId`, when given, pins the graph to the exact revision that
// session ran against, instead of the current draft. The "States" tab
// passes the session under review; EditProjectView omits it.
export function getProjectGraph(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/graph${query}`)
}


// Declared env-key definitions (name/ui_description/value) of the
// project's top-level `env:` section.
export function getProjectEnvKeys(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys${query}`)
}

// The optional top-level `project:` section (id/ui_label/ui_description).
export function getProjectMetadata(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/project`)
}

// Declared source definitions (name/ui_label/ui_description/url) of the
// project's top-level `sources:` section.
export function getProjectSources(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources${query}`)
}

// ShareProjectDialog.vue's own trigger — a fresh Invite row every time
// the dialog opens (see backend's InviteManager.create_invite), never
// reused. { code, expires_at, max_shares }.
export function postCreateInvite(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/invites`, { method: 'POST' })
}



export function putProjectField(projectId, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/project/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// One service's own level for this project: 'required', 'optional' (the
// default, which removes the declaration) or 'disabled' — see
// PROJECT_SPECS.md §1.2. One service at a time, never a whole mapping
// assembled here.
export function putServiceLevel(projectId, service, level) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/services/${encodeURIComponent(service)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ level }) }
  )
}



export function getProjectFiles(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files`)
}


// {content, can_undo, can_redo} of fileName's current content —
// can_undo/can_redo drive the editor's Undo/Redo buttons, scoped to the
// current user.
export function getProjectFile(projectId, fileName) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`)
}

export function putProjectFile(projectId, fileName, content) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
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
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName })
  })
}

export function postSourceWebImport(projectId, sourceName, query, onProgress) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}/web-import`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }) },
    { parse: 'sse', onProgress }
  )
}

// Image attachments: same PUT route as putProjectFile, but the raw File
// as body with its own Content-Type — the backend validates an image
// save against the request header, unlike a text save.
export function putProjectFileBinary(projectId, fileName, file) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file
  })
}


// A pure editor preview, not a save — nothing is persisted. `content` is
// the editor's current text, needed so a later redo/undo can restore it;
// the backend still decides what to restore. Response: {content, can_undo, can_redo}.
export function undoProjectFile(projectId, fileName, content) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/undo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function redoProjectFile(projectId, fileName, content) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/redo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

// A pure editor preview, same shape as undo/redo above — nothing is
// persisted. Response: {content} — the new file text for the caller to
// drop into its own (unsaved) editor buffer.
function aiEditProjectFile(projectId, fileName, instruction) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${fileName}/ai-edit`,
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
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
}

export function postAddLegalTerms(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/legal-terms`, { method: 'POST' })
}

export function postAddState(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states`, { method: 'POST' })
}

export function postAddSignal(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/signals`, { method: 'POST' })
}

export function postAddEnvKey(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys`, { method: 'POST' })
}

export function postAddSource(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources`, { method: 'POST' })
}

export function postAddSourceFromFile(projectId, fileName, content) {
  const query = `?file_name=${encodeURIComponent(fileName)}`
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources${query}`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function postAddAction(projectId, stateName) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions`,
    { method: 'POST' }
  )
}

export function putStateField(projectId, stateName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putActionField(projectId, stateName, actionName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// The init-action lives outside `states:` in the YAML, so unlike
// putActionField it isn't looked up inside a state's `actions:` list —
// every editable field goes through this dedicated endpoint instead.
export function putInitActionField(projectId, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/init-action/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSignalField(projectId, signalName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putEnvKeyField(projectId, envKeyName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys/${encodeURIComponent(envKeyName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSourceField(projectId, sourceName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

// 0-based index the action should end up at, within its own state's
// actions list.
export function putActionOrder(projectId, stateName, actionName, position) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/order`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: position }) }
  )
}

export function deleteState(projectId, stateName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectAction(projectId, stateName, actionName) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}`,
    { method: 'DELETE' }
  )
}

export function deleteProjectSignal(projectId, signalName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectEnvKey(projectId, envKeyName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys/${encodeURIComponent(envKeyName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectSource(projectId, sourceName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}`, {
    method: 'DELETE'
  })
}

export function getProjectRevision(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/revision`)
}

export function getPublishPreview(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/publish/preview`)
}

export function postPublishProject(projectId, remapTo = null) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remap_to: remapTo })
  })
}

export function postRevertProject(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/revert`, { method: 'POST' })
}

