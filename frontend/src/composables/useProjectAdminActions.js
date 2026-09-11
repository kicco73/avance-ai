import { ref } from 'vue'
import {
  getState, putProject, postNewProject, activateProject, deleteProject, postWipeAllLiveSessions,
  postCleanUnusedRevisions, downloadProject, getBackup, postRestoreBackup, getAbout,
  getPublishPreview, postPublishProject
} from '../api.js'
import { postBuildLocalModule } from '../api/build.js'
import { buildAvailable } from '../buildAvailability.js'
import PublishRemapDialog from '../components/settings/PublishRemapDialog.vue'
import { aboutDialog, confirmDialog, customDialog, infoDialog } from '../dialogStore.js'
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

// askPublishConsent's "the user backed out" — distinct from the null
// that means "no remap needed", which is a perfectly good publish.
const CANCELLED = Symbol('publish cancelled')

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

  // Manage projects' one Publish button: the draft becomes the published
  // revision, and where this backend can compile at all (see
  // buildAvailability.js) that revision is compiled into a package right
  // after. A backend without the build package publishes and stops there.
  async function handlePublishProject(projectId) {
    const remapTo = await askPublishConsent(projectId)
    if (remapTo === CANCELLED) return
    let published
    try {
      published = await postPublishProject(projectId, remapTo)
    } catch {
      return
    }
    const built = await compilePublishedRevision(projectId)
    await refreshStateAndProjects()
    await infoDialog({ title: 'Publish', body: publishReport(published, built) })
  }

  // The chosen remap target, null when none is needed, or CANCELLED when
  // the user backed out of either question.
  async function askPublishConsent(projectId) {
    let preview
    try {
      preview = await getPublishPreview(projectId)
    } catch {
      return CANCELLED
    }
    if (preview.needs_remap) {
      return await customDialog({ component: PublishRemapDialog, props: { prompt: preview } }) ?? CANCELLED
    }
    // Only ask when it's actually consequential — a live conversation
    // still running on the currently published revision.
    if (!preview.has_active_sessions) return null
    const ok = await confirmDialog({
      title: 'Publish',
      body: "Publish this project's current revision? There's an active session on the currently "
        + 'published revision — it will stay frozen there; this one becomes the new one.',
      okLabel: 'Publish',
      danger: true
    })
    return ok ? null : CANCELLED
  }

  // What was built, or null — a backend that cannot compile, and a
  // compile that failed (already surfaced via apiFetch), read the same
  // here: the publish itself stands either way.
  async function compilePublishedRevision(projectId) {
    if (!buildAvailable.value) return null
    try {
      return await postBuildLocalModule(projectId)
    } catch {
      return null
    }
  }

  function publishReport(published, built) {
    const head = `Published revision ${published.published_revision}.`
    return built ? `${head} Compiled into ${built.module}.` : head
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
    activateAndRefresh, handleModelDownload, handleModelDelete, handlePublishProject, handleWipeAllLiveSessions,
    handleCleanUnusedRevisions, handleDownloadBackup, handleRestoreBackup, handleShowAbout,
  }
}
