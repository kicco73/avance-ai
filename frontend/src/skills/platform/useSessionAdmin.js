import { ref } from 'vue'
import {
  postImportSessions, getExportSessions, deleteImportedSessions, putSessionsReassign,
  deleteTestUser, deleteUserSessions, deleteSession,
} from './api.js'
import { sessions, refreshSessionsQuietly } from '../../chatStore.js'
import { summarizeImportFailures } from '../../sessionImport.js'
import { setApiError, clearApiError } from '../../errorStore.js'
import { confirmDialog } from '../../dialogStore.js'

export function useSessionAdmin(projectId, currentSessionId, currentSession, currentSessionIsImported, selectSession) {
  const importingSessions = ref(false)
  const importProgress = ref(null)

  async function handleImportSession(files) {
    importingSessions.value = true
    importProgress.value = null
    let result
    try {
      result = await postImportSessions(projectId, files, (message) => {
        importProgress.value = message.percentage
      })
    } catch {
      return
    } finally {
      importingSessions.value = false
      importProgress.value = null
    }

    if (result.last_session_id != null) {
      await refreshSessionsQuietly(true, projectId)
      const imported = sessions.value.find((s) => s.id === result.last_session_id)
      if (imported) selectSession(imported)
    }

    const failureSummary = summarizeImportFailures(result.results)
    if (failureSummary) setApiError(failureSummary.message, failureSummary.detail)
    else clearApiError()
  }

  async function onMoveSessions({ sessionIds, username }) {
    try {
      await putSessionsReassign(projectId, sessionIds, username)
      await refreshSessionsQuietly(true, projectId)
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
      await deleteTestUser(projectId, testUserSeq)
      if (currentSession.value?.username === deletedUsername) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectId)
    } catch {
    }
  }

  async function onDeleteUserSessions({ username }) {
    const ok = await confirmDialog({
      title: 'Delete sessions',
      body: `Delete every imported session from "${username}"? This cannot be undone.`,
      okLabel: 'Delete',
      danger: true
    })
    if (!ok) return
    try {
      await deleteUserSessions(projectId, username)
      if (currentSession.value?.username === username) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectId)
    } catch {
    }
  }

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
      await deleteImportedSessions(projectId)
      if (currentSessionIsImported.value) currentSessionId.value = null
      await refreshSessionsQuietly(true, projectId)
    } catch {
    } finally {
      deletingAllImported.value = false
    }
  }

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
      await refreshSessionsQuietly(true, projectId)
    } catch {
    } finally {
      deletingSessionId.value = null
    }
  }

  const downloadingSessions = ref(false)
  async function handleDownloadSessions(type) {
    downloadingSessions.value = true
    try {
      const blob = await getExportSessions(projectId, type)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${projectId}-${type}-sessions.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch {
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
