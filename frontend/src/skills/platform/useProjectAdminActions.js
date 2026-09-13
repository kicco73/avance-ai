import { ref } from 'vue'
import {
  getState, putProject, postNewProject, activateProject, deleteProject, downloadProject,
  getPublishPreview, postPublishProject
} from './api.js'
import PublishRemapDialog from './components/settings/PublishRemapDialog.vue'
import { confirmDialog, customDialog, infoDialog } from '../../dialogStore.js'
import { handleStateChange, loadMessages, clearChatUi } from '../../chatStore.js'
import { emitProjectsChanged } from '../../projectChangeEvents.js'

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

export function useProjectAdminActions() {
  const modelUploadInput = ref(null)
  const uploadingProject = ref(false)
  const uploadProgress = ref(null)
  const uploadProjectId = ref(null)
  const uploadIconReady = ref(false)

  async function refreshStateAndProjects() {
    const newState = await getState()
    await emitProjectsChanged()
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
      await loadMessages(projectId)
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
  // revision. A backend that can compile turns it into a package as part
  // of answering (see bus.POINT_PROJECT_PUBLISHED) and says so in `built`;
  // one that cannot publishes and stops there.
  async function handlePublishProject(projectId) {
    const remapTo = await askPublishConsent(projectId)
    if (remapTo === CANCELLED) return
    let published
    try {
      published = await postPublishProject(projectId, remapTo)
    } catch {
      return
    }
    await refreshStateAndProjects()
    await infoDialog({ title: 'Publish', body: publishReport(published) })
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

  function publishReport(published) {
    const head = `Published revision ${published.published_revision}.`
    return published.built ? `${head} Compiled into ${published.built.module}.` : head
  }


  return {
    modelUploadInput, uploadingProject, uploadProgress, uploadProjectId, uploadIconReady,
    triggerModelUpload, handleNewProject, handleModelUploadChange, handleModelEditSaved, handleProjectSwitch,
    activateAndRefresh, handleModelDownload, handleModelDelete, handlePublishProject,
  }
}
