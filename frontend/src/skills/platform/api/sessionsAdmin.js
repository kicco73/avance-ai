import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function postImportSessions(projectId, files, onProgress) {
  const formData = new FormData()
  for (const file of files) formData.append('files', file)
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sessions/import`, {
    method: 'POST',
    body: formData
  }, { parse: 'sse', onProgress })
}

export function getExportSessions(projectId, type) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sessions/export?type=${encodeURIComponent(type)}`,
    {}, { parse: 'blob' }
  )
}

export function deleteImportedSessions(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sessions/imported`, {
    method: 'DELETE'
  })
}

export function putSessionsReassign(projectId, sessionIds, username) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sessions/reassign`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_ids: sessionIds, username })
  })
}

export function deleteTestUser(projectId, testUserSeq) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/test-users/${encodeURIComponent(testUserSeq)}`, {
    method: 'DELETE'
  })
}

export function deleteUserSessions(projectId, username) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sessions/users/${encodeURIComponent(username)}`, {
    method: 'DELETE'
  })
}
