import { computed, onBeforeUnmount, ref } from 'vue'
import { getProjectSources, postAddSource, postAddSourceFromFile, postAddWebSearchSource, putSourceField, deleteProjectSource } from './api.js'
import { onProjectChanged } from '../../projectChangeEvents.js'

function sourceNameHint(fileName) {
  return fileName.replace(/\.[^./]+$/, '')
}

export function useProjectSources(projectId, guardedAction, flashRecentlyAdded) {
  const sourcesLoading = ref(true)
  const sources = ref([])
  const currentSourceName = ref(null)
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
    } finally {
      sourcesLoading.value = false
    }
  }

  onBeforeUnmount(onProjectChanged((changedProjectId) => {
    if (changedProjectId === projectId) return loadSources()
  }))

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
        sourcesRootSelected.value = false
        currentSourceName.value = source.name
        flashRecentlyAdded(`source:${source.name}`)
      } catch {
      }
    })
  }

  function handleAddWebSearchSource() {
    guardedAction('add a new web search source', async () => {
      try {
        const source = await postAddWebSearchSource(projectId)
        sourcesRootSelected.value = false
        currentSourceName.value = source.name
        flashRecentlyAdded(`source:${source.name}`)
      } catch {
      }
    })
  }

  function handleUploadSourceFile(file) {
    return guardedAction('add a new source', async () => {
      try {
        const text = await file.text()
        const source = await postAddSourceFromFile(projectId, sourceNameHint(file.name), text)
        sourcesRootSelected.value = false
        currentSourceName.value = source.name
        flashRecentlyAdded(`source:${source.name}`)
      } catch {
      }
    })
  }

  function handleSetSourceField(field, value) {
    const name = currentSourceName.value
    if (!name) return
    guardedAction(`edit "${field}"`, async () => {
      try {
        const source = await putSourceField(projectId, name, field, value)
        currentSourceName.value = source.name
      } catch {
      }
    })
  }

  function handleDeleteSource(name) {
    guardedAction('delete this source', async () => {
      deletingSource.value = name
      try {
        await deleteProjectSource(projectId, name)
        if (currentSourceName.value === name) {
          currentSourceName.value = null
          sourcesRootSelected.value = true
        }
      } catch {
      } finally {
        deletingSource.value = null
      }
    })
  }

  return {
    sourcesLoading, sources, currentSourceName, sourcesRootSelected, selectedSource, deletingSource,
    loadSources, selectSource, selectSourcesRoot, handleAddSource, handleAddWebSearchSource, handleUploadSourceFile,
    handleSetSourceField, handleDeleteSource,
  }
}
