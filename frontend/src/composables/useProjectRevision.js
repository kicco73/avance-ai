import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjectRevision, postRevertProject } from '../api.js'
import { confirmDialog } from '../dialogStore.js'

// EditProjectView.vue's revision control: which revision the draft is,
// which one is published, and the one action the editor still owns over
// that pair — revert. Publishing itself is not here and not in the editor
// at all: it belongs to Manage projects' Publish button, which publishes
// and then compiles (see useProjectAdminActions.js). `currentFileName`/
// `activeEditor` are useProjectFiles.js's own (only consulted to reload a
// non-index.yml editor's history after a real revert); `selectedGraphElement`
// is the Inspector's graph selection, cleared on revert since it can name
// a state the reverted revision no longer has.
export function useProjectRevision(projectId, currentFileName, activeEditor, selectedGraphElement) {
  // {revision, published_revision} — null while not yet loaded. A save can
  // fork (see Db.save_project_files' fork-on-first-edit-after-publish),
  // bumping `revision` — refreshed after every save.
  const projectRevision = ref(null)
  const reverting = ref(false)

  async function refreshProjectRevision() {
    try {
      projectRevision.value = await getProjectRevision(projectId)
    } catch {
      // already surfaced via apiFetch
    }
  }

  // A draft ahead of what's published — the whole reason the menu has
  // anything in it.
  const hasUnpublishedChanges = computed(
    () => projectRevision.value != null && projectRevision.value.revision !== projectRevision.value.published_revision
  )

  // A revert invalidates every user's undo/redo history server-side;
  // refreshAfterProjectEdit already re-pulls index.yml's buffer, so this
  // only matters when a *different* file is open.
  async function refreshActiveEditorHistory() {
    if (currentFileName.value === 'index.yml') return
    await activeEditor()?.reload?.()
  }

  // The "Rev. X" button's dropdown holds exactly one entry, so it only
  // opens when that entry is there: a draft ahead of the published
  // revision, and a prior publication to go back to.
  const canRevert = computed(
    () => hasUnpublishedChanges.value && projectRevision.value?.published_revision != null
  )
  const revisionMenuOpen = ref(false)
  function closeRevisionMenu() {
    revisionMenuOpen.value = false
  }
  function handleDocumentClickForRevisionMenu(event) {
    if (revisionMenuOpen.value && !event.target.closest('.revision-menu')) closeRevisionMenu()
  }
  onMounted(() => document.addEventListener('click', handleDocumentClickForRevisionMenu))
  onBeforeUnmount(() => document.removeEventListener('click', handleDocumentClickForRevisionMenu))

  async function handleRevert() {
    if (!canRevert.value || reverting.value) return
    const targetRevision = projectRevision.value.published_revision
    const ok = await confirmDialog({
      title: 'Revert',
      body: `Revert to rev. ${targetRevision}? This permanently discards every unpublished change on rev. ${projectRevision.value.revision} — there's no undo for this.`,
      okLabel: 'Revert',
      danger: true
    })
    if (!ok) return
    reverting.value = true
    try {
      await postRevertProject(projectId)
      selectedGraphElement.value = null
      await refreshActiveEditorHistory()
    } catch {
      // already surfaced via apiFetch
    } finally {
      reverting.value = false
    }
  }

  return {
    projectRevision, reverting, refreshProjectRevision,
    canRevert, revisionMenuOpen, closeRevisionMenu, handleRevert,
  }
}
