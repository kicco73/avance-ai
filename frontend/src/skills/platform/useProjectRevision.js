import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjectRevision, postRevertProject } from './api.js'
import { confirmDialog } from '../../dialogStore.js'
import { onProjectChanged } from '../../projectChangeEvents.js'

export function useProjectRevision(projectId, currentFileName, activeEditor, selectedGraphElement) {
  const projectRevision = ref(null)
  const reverting = ref(false)

  async function refreshProjectRevision() {
    try {
      projectRevision.value = await getProjectRevision(projectId)
    } catch {
    }
  }

  onBeforeUnmount(onProjectChanged((changedProjectId) => {
    if (changedProjectId === projectId) return refreshProjectRevision()
  }))

  const hasUnpublishedChanges = computed(
    () => projectRevision.value != null && projectRevision.value.revision !== projectRevision.value.published_revision
  )

  async function refreshActiveEditorHistory() {
    if (currentFileName.value === 'index.yml') return
    await activeEditor()?.reload?.()
  }

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
    } finally {
      reverting.value = false
    }
  }

  return {
    projectRevision, reverting, refreshProjectRevision,
    canRevert, revisionMenuOpen, closeRevisionMenu, handleRevert,
  }
}
