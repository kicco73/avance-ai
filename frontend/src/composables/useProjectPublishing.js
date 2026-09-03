import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjectRevision, getPublishPreview, postPublishProject, postRevertProject } from '../api.js'
import { confirmDialog } from '../dialogStore.js'

// EditProjectView.vue's publish/revert lifecycle: the draft-vs-published
// revision, the publish flow (including its "needs remap" and "active
// session" side dialogs), and revert. `currentFileName`/`activeEditor` are
// useProjectFiles.js's own (only consulted to reload a non-index.yml
// editor's history after a real publish/revert); `selectedGraphElement`
// is the Inspector's graph selection, cleared on revert since it can name
// a state the reverted revision no longer has.
export function useProjectPublishing(projectId, currentFileName, activeEditor, selectedGraphElement) {
  // {revision, published_revision} — null while not yet loaded. A save can
  // fork (see Db.save_project_files' fork-on-first-edit-after-publish),
  // bumping `revision` — refreshed after every save and every publish.
  const projectRevision = ref(null)
  const publishing = ref(false)
  // Set only while ProjectService.preview_publish reported needs_remap —
  // the modal below is shown exactly while this is non-null. Cleared on
  // both confirm and cancel.
  const publishRemapPrompt = ref(null)
  const publishRemapChoice = ref('')
  // Set only while leaveEditProject's "publish before leaving?" confirm was
  // accepted — holds whatever navigation was actually requested (Back, or
  // one of the Settings-menu items), so handlePublish's success paths can
  // carry it out once the publish actually lands. Every other exit clears
  // this instead, so a later, unrelated Publish click never navigates away too.
  const pendingLeaveAction = ref(null)

  async function refreshProjectRevision() {
    try {
      projectRevision.value = await getProjectRevision(projectId)
    } catch {
      // already surfaced via apiFetch
    }
  }

  const publishUpToDate = computed(
    () => projectRevision.value != null && projectRevision.value.revision === projectRevision.value.published_revision
  )

  // A real publish/revert invalidates every user's undo/redo history
  // server-side; refreshAfterProjectEdit already re-pulls index.yml's
  // buffer, so this only matters when a *different* file is open.
  async function refreshActiveEditorHistory() {
    if (currentFileName.value === 'index.yml') return
    await activeEditor()?.reload?.()
  }

  // Carries out whatever navigation leaveEditProject asked for once a
  // publish it required actually lands (see pendingLeaveAction) — called
  // by both handlePublish's direct-success path and confirmPublishRemap's.
  function runPendingLeaveAction() {
    if (!pendingLeaveAction.value) return
    const action = pendingLeaveAction.value
    pendingLeaveAction.value = null
    action()
  }

  function resetPendingLeaveAction() {
    pendingLeaveAction.value = null
  }

  async function handlePublish() {
    if (publishUpToDate.value || publishing.value) {
      resetPendingLeaveAction()
      return
    }
    publishing.value = true
    try {
      const preview = await getPublishPreview(projectId)
      if (preview.needs_remap) {
        publishRemapChoice.value = ''
        publishRemapPrompt.value = preview
        return
      }
      // Only ask when it's actually consequential — a live conversation
      // still running on the currently published revision.
      if (preview.has_active_sessions) {
        const ok = await confirmDialog({
          title: 'Publish',
          body: `Publish revision ${projectRevision.value?.revision}? There's an active session on the currently published revision — it will stay frozen there; this one becomes the new one.`,
          okLabel: 'Publish',
          danger: true
        })
        if (!ok) {
          resetPendingLeaveAction()
          return
        }
      }
      projectRevision.value = await postPublishProject(projectId)
      await refreshActiveEditorHistory()
      runPendingLeaveAction()
    } catch {
      // already surfaced via apiFetch
      resetPendingLeaveAction()
    } finally {
      publishing.value = false
    }
  }

  async function confirmPublishRemap(stateKey) {
    publishing.value = true
    try {
      projectRevision.value = await postPublishProject(projectId, stateKey)
      publishRemapPrompt.value = null
      await refreshActiveEditorHistory()
      runPendingLeaveAction()
    } catch {
      // already surfaced via apiFetch — leave the modal open so the user
      // can pick a different state or cancel
    } finally {
      publishing.value = false
    }
  }

  function cancelPublishRemap() {
    publishRemapPrompt.value = null
    publishing.value = false
    resetPendingLeaveAction()
  }

  // The "Rev. X" split button's dropdown arrow — only rendered when
  // there's both a draft ahead of the published revision and a prior
  // publication to revert to.
  const canRevert = computed(
    () => !publishUpToDate.value && projectRevision.value?.published_revision != null
  )
  const publishMenuOpen = ref(false)
  function closePublishMenu() {
    publishMenuOpen.value = false
  }
  function handleDocumentClickForPublishMenu(event) {
    if (publishMenuOpen.value && !event.target.closest('.publish-split-btn')) closePublishMenu()
  }
  onMounted(() => document.addEventListener('click', handleDocumentClickForPublishMenu))
  onBeforeUnmount(() => document.removeEventListener('click', handleDocumentClickForPublishMenu))

  async function handleRevert() {
    if (!canRevert.value || publishing.value) return
    const targetRevision = projectRevision.value.published_revision
    const ok = await confirmDialog({
      title: 'Revert',
      body: `Revert to rev. ${targetRevision}? This permanently discards every unpublished change on rev. ${projectRevision.value.revision} — there's no undo for this.`,
      okLabel: 'Revert',
      danger: true
    })
    if (!ok) return
    publishing.value = true
    try {
      await postRevertProject(projectId)
      selectedGraphElement.value = null
      await refreshActiveEditorHistory()
    } catch {
      // already surfaced via apiFetch
    } finally {
      publishing.value = false
    }
  }

  return {
    projectRevision, publishing, publishRemapPrompt, publishRemapChoice, pendingLeaveAction,
    refreshProjectRevision, publishUpToDate, resetPendingLeaveAction,
    handlePublish, confirmPublishRemap, cancelPublishRemap,
    canRevert, publishMenuOpen, closePublishMenu, handleRevert,
  }
}
