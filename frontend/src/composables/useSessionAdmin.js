import { ref } from 'vue'
import {
  postImportSessions, getExportSessions, deleteImportedSessions, putSessionsReassign,
  deleteTestUser, deleteUserSessions, deleteSession,
} from '../api.js'
import { sessions, refreshSessionsQuietly } from '../chatStore.js'
import { summarizeImportFailures } from '../sessionImport.js'
import { setApiError, clearApiError } from '../errorStore.js'
import { confirmDialog } from '../dialogStore.js'

// The "Label sessions" view's own session-administration actions: import,
// move between users, and every delete flow. `currentSessionId`/
// `currentSession`/`currentSessionIsImported`/`selectSession` are owned by
// the caller — this composable only reads/drives them where an action
// needs to clear or move the active selection.
export function useSessionAdmin(projectName, currentSessionId, currentSession, currentSessionIsImported, selectSession) {
  // Every selected file (whichever mix of .txt transcripts and "Download
  // all" .json exports) uploaded in one request — all per-file/per-session
  // dispatch and error handling happens server-side; this just renders the
  // returned result.
  const importingSessions = ref(false)
  // null until the first SSE progress chunk arrives — SessionsTree.vue
  // shows the indeterminate spinner until then, a filling ring after.
  const importProgress = ref(null)

  async function handleImportSession(files) {
    importingSessions.value = true
    importProgress.value = null
    let result
    try {
      result = await postImportSessions(projectName, files, (message) => {
        importProgress.value = message.percentage
      })
    } catch {
      // already surfaced via apiFetch
      return
    } finally {
      importingSessions.value = false
      importProgress.value = null
    }

    if (result.last_session_id != null) {
      // The list must contain the new session before it can be looked up
      // in it — refresh first, select second, not the other way around.
      await refreshSessionsQuietly(true, projectName)
      const imported = sessions.value.find((s) => s.id === result.last_session_id)
      if (imported) selectSession(imported)
    }

    const failureSummary = summarizeImportFailures(result.results)
    if (failureSummary) setApiError(failureSummary.message, failureSummary.detail)
    else clearApiError()
  }

  async function onMoveSessions({ sessionIds, username }) {
    try {
      await putSessionsReassign(projectName, sessionIds, username)
      await refreshSessionsQuietly(true, projectName)
    } catch {
    }
  }

  async function onDeleteTestUser({ testUserSeq }) {
    const ok = await confirmDialog({
      title: 'Delete test user',
      body: `Delete Test User ${testUserSeq} and all of their sessions? This cannot be undone.`,
      okLabel: 'Delete',
      danger: true
    })
    if (!ok) return
    const deletedUsername = `Test user ${testUserSeq}`
    try {
      await deleteTestUser(projectName, testUserSeq)
      if (currentSession.value?.username === deletedUsername) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectName)
    } catch {
    }
  }

  // Any other non-live branch's own × button (see SessionsTree.vue's
  // isDeletableBranch) — an arbitrary imported username, not a "Test user N" one.
  async function onDeleteUserSessions({ username }) {
    const ok = await confirmDialog({
      title: 'Delete sessions',
      body: `Delete every imported session from "${username}"? This cannot be undone.`,
      okLabel: 'Delete',
      danger: true
    })
    if (!ok) return
    try {
      await deleteUserSessions(projectName, username)
      if (currentSession.value?.username === username) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectName)
    } catch {
    }
  }

  // The sessions panel's own "Delete all imported sessions" icon — every
  // imported session of the project, across every user.
  const deletingAllImported = ref(false)
  async function handleDeleteAllImported() {
    const ok = await confirmDialog({
      title: 'Delete all imported sessions',
      body: 'Delete every imported session of this project? This cannot be undone.',
      okLabel: 'Delete',
      danger: true
    })
    if (!ok) return
    deletingAllImported.value = true
    try {
      await deleteImportedSessions(projectName)
      if (currentSessionIsImported.value) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectName)
    } catch {
      // already surfaced via apiFetch
    } finally {
      deletingAllImported.value = false
    }
  }

  // Only an imported session is ever deletable here — a live/native one
  // is the record of a real conversation, not this view's to discard.
  const deletingSessionId = ref(null)
  async function handleDeleteSession(session) {
    const ok = await confirmDialog({
      title: 'Delete session',
      body: `Delete this imported session (${session.title || session.end_state})? This cannot be undone.`,
      okLabel: 'Delete',
      danger: true
    })
    if (!ok) return
    deletingSessionId.value = session.id
    try {
      await deleteSession(session.id)
      if (session.id === currentSessionId.value) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectName)
    } catch {
      // already surfaced via apiFetch
    } finally {
      deletingSessionId.value = null
    }
  }

  // Every session of this project as one .json file, re-uploadable through
  // this view's own Import button. Same synthetic-<a> download trick as
  // App.vue's handleModelDownload.
  const downloadingSessions = ref(false)
  // `type` ('live' | 'imported') comes from SessionsTree.vue's own active
  // tab (see its 'download-all' emit) — Download all only ever exports
  // whichever kind is currently showing.
  async function handleDownloadSessions(type) {
    downloadingSessions.value = true
    try {
      const blob = await getExportSessions(projectName, type)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${projectName}-${type}-sessions.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch {
      // already surfaced via apiFetch
    } finally {
      downloadingSessions.value = false
    }
  }

  return {
    importingSessions, importProgress, handleImportSession,
    onMoveSessions, onDeleteTestUser, onDeleteUserSessions,
    deletingAllImported, handleDeleteAllImported,
    deletingSessionId, handleDeleteSession,
    downloadingSessions, handleDownloadSessions,
  }
}
