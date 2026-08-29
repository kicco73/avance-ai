import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The "Label sessions" view's own Import button — every selected file in
// one request, whichever mix of a .txt transcript and a "Download all"
// .json export it contains. All per-file/per-session dispatch and error
// handling happens server-side. Streams SSE progress chunks within this
// same request/response (see post_import_sessions); pass `onProgress
// (message)` to render live percentage instead of a spinner. The
// returned promise resolves with the final {results, last_session_id}.
export function postImportSessions(projectName, files, onProgress) {
  const formData = new FormData()
  for (const file of files) formData.append('files', file)
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/import`, {
    method: 'POST',
    body: formData
  }, { parse: 'sse', onProgress })
}

// The "Label sessions" view's own "Download all" button — every session
// of `projectName` matching `type` ('live' | 'imported'), as one JSON
// array. A blob so the caller can trigger a real file download.
export function getExportSessions(projectName, type) {
  return apiFetch(
    `${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/export?type=${encodeURIComponent(type)}`,
    {}, { parse: 'blob' }
  )
}

// The "Label sessions" view's own "Delete all imported sessions" button —
// every imported session of `projectName`, across every user.
export function deleteImportedSessions(projectName) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/imported`, {
    method: 'DELETE'
  })
}

// SessionsTree.vue's own drag-and-drop between branches — `username` is
// whichever branch the sessions were dropped on, a "Test user N" one or
// any other imported username alike.
export function putSessionsReassign(projectName, sessionIds, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/reassign`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_ids: sessionIds, username })
  })
}

export function deleteTestUser(projectName, testUserSeq) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/test-users/${encodeURIComponent(testUserSeq)}`, {
    method: 'DELETE'
  })
}

// The "Label sessions" view's per-branch × button for any non-live
// branch that isn't a "Test user N" one — an arbitrary imported username.
export function deleteUserSessions(projectName, username) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectName)}/sessions/users/${encodeURIComponent(username)}`, {
    method: 'DELETE'
  })
}
