import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getBackup(onProgress) {
  return apiFetch(`${API_URL}/skills/platform/settings/backup`, {}, { parse: 'blob', onProgress })
}

export function postRestoreBackup(file) {
  return apiFetch(`${API_URL}/skills/platform/settings/backup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file
  })
}

export function postWipeAllLiveSessions() {
  return apiFetch(`${API_URL}/skills/platform/settings/database/wipe-live-sessions`, { method: 'POST' })
}

export function postCleanUnusedRevisions() {
  return apiFetch(`${API_URL}/skills/platform/settings/database/clean-unused-revisions`, { method: 'POST' })
}
