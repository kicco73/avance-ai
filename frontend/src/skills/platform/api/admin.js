import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'


export function getProjectsRuntimeStatus() {
  return apiFetch(`${API_URL}/skills/platform/settings/projects/runtime-status`)
}

export function getManagedProjectApps() {
  return apiFetch(`${API_URL}/skills/platform/settings/projects/apps`)
}


export function putUserRole(userId, role) {
  return apiFetch(`${API_URL}/skills/platform/users/${encodeURIComponent(userId)}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role })
  })
}

export function putProjectPause(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/pause`, { method: 'POST' })
}

export function putProjectResume(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/resume`, { method: 'POST' })
}

export function postNewProject() {
  return apiFetch(`${API_URL}/skills/platform/projects`, { method: 'POST' })
}





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

