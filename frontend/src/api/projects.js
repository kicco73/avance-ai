import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'


export function activateProject(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
}

export function postRedeemInviteCode(code) {
  return apiFetch(`${API_URL}/core/projects/invitations/${encodeURIComponent(code)}`, { method: 'POST' })
}

export function projectFileContentUrl(projectId, fileName, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return `${API_URL}/core/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/content${query}`
}

export function getSessionSignals(sessionId) {
  return apiFetch(`${API_URL}/core/sessions/${encodeURIComponent(sessionId)}/signals`)
}

export function getStateInputTokens(projectId, stateKey, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateKey)}/tokens${query}`)
}

export function getProjectSignals(projectId, stateKey, sessionId) {
  const params = new URLSearchParams()
  if (stateKey != null) params.set('state_key', stateKey)
  if (sessionId != null) params.set('session_id', sessionId)
  const query = params.size ? `?${params}` : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/signals${query}`)
}



export function getProjectStates(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/states`)
}

export function getIdentifiers(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/identifiers`)
}


export function getProjects() {
  return apiFetch(`${API_URL}/core/projects`)
}

export function getSubscribedProjects() {
  return apiFetch(`${API_URL}/core/projects/subscribed`)
}

export function getUsers() {
  return apiFetch(`${API_URL}/core/users`)
}

export function getDoc(name) {
  return apiFetch(`${API_URL}/core/docs/${encodeURIComponent(name)}`)
}

export function getProjectFileTypes() {
  return apiFetch(`${API_URL}/core/projects/file-types`)
}

export function getDriveFiles(projectId, prefix) {
  const query = prefix ? `?prefix=${encodeURIComponent(prefix)}` : ''
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/drive${query}`)
}

export function driveFileContentUrl(projectId, path) {
  return `${API_URL}/core/projects/${encodeURIComponent(projectId)}/drive/${encodeURIComponent(path)}`
}

export function postSaveMediaToDrive(projectId, fileName) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/drive/save-media`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_name: fileName })
  })
}

export function postDownloadMediaToDrive(projectId, fileName) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/drive/download-media`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_name: fileName })
  })
}

export function postRecordDriveDownload(projectId, path) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/drive/${encodeURIComponent(path)}/download`, {
    method: 'POST'
  })
}
