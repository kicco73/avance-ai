import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getProjects() {
  return apiFetch(`${API_URL}/projects`)
}

// Settings > Runtime status view's own table — every project's own
// {id, status, paused_reason, revision, published_revision}.
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
export function putProjectPause(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/pause`, { method: 'PUT' })
}

export function putProjectResume(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/resume`, { method: 'PUT' })
}

// "New project" — same effect server-side as uploading samples/Hello
// world.zip by hand (see putProject), minus picking an id first (the
// backend mints a fresh one on its own — project.id must be globally unique).
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

export function activateProject(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/activate`, {
    method: 'PUT'
  })
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
  return apiFetch(`${API_URL}/projects/upload`, {
    method: 'POST',
    headers: { 'Content-Type': contentType },
    body: file
  }, { parse: 'sse', onProgress, onCommitted })
}

export function deleteProject(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE'
  })
}

// Settings > Manage services > Database — wipes live sessions across
// every project at once, not just one.
export function postWipeAllLiveSessions() {
  return apiFetch(`${API_URL}/settings/database/wipe-live-sessions`, { method: 'POST' })
}

// Settings > Manage services > Database — deletes every archive revision,
// across every project, that's neither published, the current draft, nor
// pinned by any session. Returns {success, deleted} — deleted is how many
// distinct revisions were actually removed.
export function postCleanUnusedRevisions() {
  return apiFetch(`${API_URL}/settings/database/clean-unused-revisions`, { method: 'POST' })
}

// Settings > Manage services — read-only snapshot of .config.yml's own
// service sections (see backend AppConfig.public_services_snapshot).
export function getServicesConfig() {
  return apiFetch(`${API_URL}/settings/services`)
}

// Settings > Manage services > AI — each provider's own daily token
// spend, fetched once when the panel opens (see db/ai_usage.py):
// {today: {label: tokens}, history: [{timestamp, values: {label: tokens}}, ...]}.
export function getAiUsage() {
  return apiFetch(`${API_URL}/settings/services/ai-usage`)
}

export function downloadProject(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}`, {}, { parse: 'blob' })
}

// Settings > Manage services > Scheduler — every row of the Task table,
// soonest run_at first (see db/tasks.py's list_tasks).
export function getScheduledTasks() {
  return apiFetch(`${API_URL}/settings/tasks`)
}
