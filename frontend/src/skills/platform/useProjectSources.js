import { computed, ref } from 'vue'
import { getProjectSources, postAddSource, postAddSourceFromFile, putSourceField, deleteProjectSource } from '../api.js'

function sourceNameHint(fileName) {
  return fileName.replace(/\.[^./]+$/, '')
}

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
  // True while the "Sources" branch header itself is the selection — no
  // individual source chosen yet (see FileExplorer.vue's own header
  // click). Distinct from currentSourceName being merely null/falsy, so
  // ProjectDesignPanel.vue's file-view conditions and InspectorStateTab.vue's
  // isBehaviorContext can tell "nothing to do with Sources" apart from
  // "Sources itself, no pick made" without a false-negative on either.
  const sourcesRootSelected = ref(false)
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
    sourcesRootSelected.value = false
    currentSourceName.value = name
  }

  function selectSourcesRoot() {
    currentSourceName.value = null
    sourcesRootSelected.value = true
  }

  function handleAddSource() {
    guardedAction('add a new source', async () => {
      try {
        const source = await postAddSource(projectId)
        await loadSources()
        sourcesRootSelected.value = false
        currentSourceName.value = source.name
        flashRecentlyAdded(`source:${source.name}`)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleUploadSourceFile(file) {
    return guardedAction('add a new source', async () => {
      try {
        const text = await file.text()
        const source = await postAddSourceFromFile(projectId, sourceNameHint(file.name), text)
        await loadSources()
        sourcesRootSelected.value = false
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
        if (currentSourceName.value === name) {
          currentSourceName.value = null
          sourcesRootSelected.value = true
        }
      } catch {
        // already surfaced via apiFetch
      } finally {
        deletingSource.value = null
      }
    })
  }

  return {
    sourcesLoading, sources, currentSourceName, sourcesRootSelected, selectedSource, deletingSource,
    loadSources, selectSource, selectSourcesRoot, handleAddSource, handleUploadSourceFile, handleSetSourceField, handleDeleteSource,
  }
}
