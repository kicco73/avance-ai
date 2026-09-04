import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getAppStoreApps() {
  return apiFetch(`${API_URL}/app-store/apps`)
}

export function postInstallApp(appId) {
  return apiFetch(`${API_URL}/app-store/apps/${encodeURIComponent(appId)}/install`, { method: 'POST' })
}

export function deleteInstallApp(appId) {
  return apiFetch(`${API_URL}/app-store/apps/${encodeURIComponent(appId)}/install`, { method: 'DELETE' })
}

export function appStoreFileContentUrl(appId, fileName) {
  return `${API_URL}/app-store/apps/${encodeURIComponent(appId)}/files/${encodeURIComponent(fileName)}/content`
}

export function getAppPreviewTranscript(appId) {
  return apiFetch(`${API_URL}/app-store/apps/${encodeURIComponent(appId)}/preview-transcript`)
}

export function postCreatePreviewSession(appId) {
  return apiFetch(`${API_URL}/app-store/apps/${encodeURIComponent(appId)}/preview-sessions`, { method: 'POST' })
}

export function getCurrentPreviewSession(sessionId, appId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/app-store/apps/${encodeURIComponent(appId)}/preview-sessions/current${query}`)
}

export function deletePreviewSessionEnv(sessionId) {
  return apiFetch(`${API_URL}/app-store/preview-sessions/${encodeURIComponent(sessionId)}/env`, { method: 'DELETE' })
}
