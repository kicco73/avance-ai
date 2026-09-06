import { ref } from 'vue'
import {
  getState, putProject, postNewProject, activateProject, deleteProject, postWipeAllLiveSessions,
  postCleanUnusedRevisions, downloadProject, getBackup, postRestoreBackup, getAbout
} from '../api.js'
import { aboutDialog, confirmDialog, infoDialog } from '../dialogStore.js'
import { handleStateChange, loadMessages, clearChatUi } from '../chatStore.js'

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function useProjectAdminActions(chatWindowRef, manageProjectsView) {
  const modelUploadInput = ref(null)
  const uploadingProject = ref(false)
  const uploadProgress = ref(null)
  const uploadProjectId = ref(null)
  const uploadIconReady = ref(false)

  async function refreshStateAndProjects() {
    const newState = await getState()
    chatWindowRef.value?.refreshProjectsMenu()
    manageProjectsView.value?.refresh()
    handleStateChange(newState)
  }

  function triggerModelUpload() {
    if (uploadingProject.value) return
    modelUploadInput.value?.click()
  }

  async function handleNewProject() {
    clearChatUi()
    try {
      await postNewProject()
      await refreshStateAndProjects()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleModelUploadChange(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    clearChatUi()
    uploadingProject.value = true
    uploadProgress.value = null
    uploadProjectId.value = null
    uploadIconReady.value = false
    try {
      const result = await putProject(file, (message) => { uploadProgress.value = message.percentage })
      uploadProjectId.value = result.project_id
      uploadIconReady.value = true
      await refreshStateAndProjects()
    } catch (err) {
      if (err.status === 400) await infoDialog({ title: 'Import rejected', body: err.message })
    } finally {
      uploadingProject.value = false
      uploadProgress.value = null
      uploadProjectId.value = null
      uploadIconReady.value = false
    }
  }

  async function handleModelEditSaved() {
    clearChatUi()
    try {
      await refreshStateAndProjects()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function activateAndRefresh(projectId) {
    clearChatUi()
    try {
      await activateProject(projectId)
      await refreshStateAndProjects()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleProjectSwitch(projectId) {
    clearChatUi()
    try {
      await activateProject(projectId)
      await refreshStateAndProjects()
      await loadMessages()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleModelDownload(projectId) {
    try {
      downloadBlob(await downloadProject(projectId), `${projectId}.zip`)
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleModelDelete(projectId) {
    clearChatUi()
    try {
      await deleteProject(projectId)
      await refreshStateAndProjects()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleWipeAllLiveSessions() {
    try {
      await postWipeAllLiveSessions()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleCleanUnusedRevisions() {
    let deleted
    try {
      ({ deleted } = await postCleanUnusedRevisions())
    } catch {
      return
    }
    await infoDialog({
      title: 'Clean unused revisions',
      body: deleted > 0 ? `Deleted ${deleted} unused revision${deleted === 1 ? '' : 's'}.` : 'No unused revisions found.'
    })
  }

  async function handleDownloadBackup() {
    try {
      downloadBlob(await getBackup(), 'avance-backup.sqlite')
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleRestoreBackup(file) {
    const ok = await confirmDialog({
      title: 'Restore backup',
      body: 'Restore this backup? This replaces the entire working database (all projects, sessions, and messages) and cannot be undone.',
      okLabel: 'Restore',
      danger: true
    })
    if (!ok) return
    clearChatUi()
    try {
      await postRestoreBackup(file)
      await refreshStateAndProjects()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleShowAbout() {
    try {
      const about = await getAbout()
      await aboutDialog({ version: about.version })
    } catch {
      // already surfaced via apiFetch
    }
  }

  return {
    modelUploadInput, uploadingProject, uploadProgress, uploadProjectId, uploadIconReady,
    triggerModelUpload, handleNewProject, handleModelUploadChange, handleModelEditSaved, handleProjectSwitch,
    activateAndRefresh, handleModelDownload, handleModelDelete, handleWipeAllLiveSessions,
    handleCleanUnusedRevisions, handleDownloadBackup, handleRestoreBackup, handleShowAbout,
  }
}
