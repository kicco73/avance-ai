import { apiFetch, projectFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

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
export function putProject(projectName, file, onProgress, onCommitted) {
  const contentType = /\.zip$/i.test(file.name) ? 'application/zip' : 'application/x-yaml'
  return projectFetch(projectName, `${API_URL}/projects/${encodeURIComponent(projectName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': contentType },
    body: file
  }, { parse: 'sse', onProgress, onCommitted })
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

export function downloadProject(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}`, {}, { parse: 'blob' })
}
