import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'


// Settings > Runtime status view's own table — every project's own
// {id, status, paused_reason, revision, published_revision, broken}.
export function getProjectsRuntimeStatus() {
  return apiFetch(`${API_URL}/skills/platform/settings/projects/runtime-status`)
}

// Manage projects' own "broken project" warnings counter/list — a
// durable record of every project_broken SystemWarning this admin has
// received, outliving the project actually being fixed (unlike
// getProjectsRuntimeStatus's own live `broken` field).
export function getProjectBrokenWarnings() {
  return apiFetch(`${API_URL}/skills/platform/settings/warnings?kind=project_broken`)
}

export function deleteProjectBrokenWarning(warningId) {
  return apiFetch(`${API_URL}/skills/platform/settings/warnings/${encodeURIComponent(warningId)}`, { method: 'DELETE' })
}


export function putUserRole(userId, role) {
  return apiFetch(`${API_URL}/skills/platform/users/${encodeURIComponent(userId)}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role })
  })
}

// Manual pause/resume — only valid from 'running'/'manually_paused'
// respectively, enforced backend-side; a 400 means the status shown was
// already stale.
export function putProjectPause(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/pause`, { method: 'POST' })
}

export function putProjectResume(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/resume`, { method: 'POST' })
}

// "New project" — same effect server-side as uploading samples/Hello
// world.zip by hand (see putProject), minus picking an id first (the
// backend mints a fresh one on its own — project.id must be globally unique).
export function postNewProject() {
  return apiFetch(`${API_URL}/skills/platform/projects`, { method: 'POST' })
}





// Streams progress SSE-style within this same response, same as
// postImportSessions — see readSseResult. `onProgress` gets each chunk's
// `percentage` (0-100) as the queued import of any bundled
// sessions.json/tests.json advances. There's no project id to pass here
// any more — the upload's own project.id is always what's used (and
// what's already published by the time this resolves, see
// ProjectManager.put_project), returned as `result.project_id`.
export function putProject(file, onProgress, onCommitted) {
  const contentType = /\.zip$/i.test(file.name) ? 'application/zip' : 'application/x-yaml'
  return apiFetch(`${API_URL}/skills/platform/projects/upload`, {
    method: 'POST',
    headers: { 'Content-Type': contentType },
    body: file
  }, { parse: 'sse', onProgress, onCommitted })
}

export function deleteProject(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE'
  })
}





export function downloadProject(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}`, {}, { parse: 'blob' })
}

