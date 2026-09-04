import { computed, ref } from 'vue'
import { getProjectSources, postAddSource, putSourceField, deleteProjectSource } from '../api.js'

// The design tree's "Sources" branch (see FileExplorer.vue) and the
// Inspector "Info" tab's own Source card: the declared-sources list, which
// one (if any) is currently selected, and add/edit/delete. Every mutating
// call routes through `guardedAction` (useProjectFiles.js's own
// unsaved-changes guard) for the same reason state/signal/env-key edits do
// (useIndexYmlEditing.js) — a source lives in the very same index.yml a
// dirty raw-text buffer could otherwise clobber.
export function useProjectSources(projectId, guardedAction, flashRecentlyAdded) {
  const sourcesLoading = ref(true)
  const sources = ref([])
  // Name of the design-tree Source node currently selected — mutually
  // exclusive with currentFileName's own selection (see EditProjectView.vue's
  // selectSource/selectFile wiring): never both truthy at once.
  const currentSourceName = ref(null)
  const deletingSource = ref(null)

  const selectedSource = computed(
    () => sources.value.find((entry) => entry.source.name === currentSourceName.value)?.source ?? null
  )

  async function loadSources() {
    sourcesLoading.value = true
    try {
      sources.value = (await getProjectSources(projectId)).sources
    } catch {
      // already surfaced via apiFetch
    } finally {
      sourcesLoading.value = false
    }
  }

  function selectSource(name) {
    currentSourceName.value = name
  }

  function handleAddSource() {
    guardedAction('add a new source', async () => {
      try {
        const source = await postAddSource(projectId)
        await loadSources()
        currentSourceName.value = source.name
        flashRecentlyAdded(`source:${source.name}`)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleSetSourceField(field, value) {
    const name = currentSourceName.value
    if (!name) return
    guardedAction(`edit "${field}"`, async () => {
      try {
        const source = await putSourceField(projectId, name, field, value)
        await loadSources()
        // Only a 'name' edit can rename the source — follow it, the same
        // way handleSetEnvKeyField re-jumps to an env key's own new name.
        currentSourceName.value = source.name
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleDeleteSource(name) {
    guardedAction('delete this source', async () => {
      deletingSource.value = name
      try {
        await deleteProjectSource(projectId, name)
        await loadSources()
        if (currentSourceName.value === name) currentSourceName.value = null
      } catch {
        // already surfaced via apiFetch
      } finally {
        deletingSource.value = null
      }
    })
  }

  return {
    sourcesLoading, sources, currentSourceName, selectedSource, deletingSource,
    loadSources, selectSource, handleAddSource, handleSetSourceField, handleDeleteSource,
  }
}
