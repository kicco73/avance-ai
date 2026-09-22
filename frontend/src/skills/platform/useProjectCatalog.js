import { onBeforeUnmount, ref } from 'vue'
import { getProjectGraph } from './api.js'
import { onProjectChanged } from '../../projectChangeEvents.js'
import { clearApiError } from '../../errorStore.js'
import { refreshIdentifierRegistry } from '../../identifierRegistry.js'
import { refreshProjectFiles } from './projectFiles.js'

export function useProjectCatalog(projectId) {
  const validStateKeys = ref(new Set())
  const availableStates = ref([])
  const projectBroken = ref(false)
  const buildProblems = ref([])
  const actionLabelsByState = ref(new Map())

  function stateLabelFor(key) {
    return availableStates.value.find((s) => s.key === key)?.uiLabel ?? key
  }

  function actionLabelFor(stateKey, actionName) {
    return actionLabelsByState.value.get(`${stateKey}::${actionName}`) ?? actionName
  }

  async function refreshCatalog() {
    try {
      const { nodes, edges } = await getProjectGraph(projectId)
      validStateKeys.value = new Set(nodes.map((n) => n.state.key))
      availableStates.value = nodes.map((n) => ({ key: n.state.key, uiLabel: n.state.ui_label, inputProcessor: n.state.input_processor }))
      actionLabelsByState.value = new Map(edges.map((e) => [`${e.source}::${e.action.name}`, e.action.ui_label]))
      buildProblems.value = []
      if (projectBroken.value) {
        projectBroken.value = false
        clearApiError()
      }
    } catch (err) {
      if (err?.code === 'project_broken') {
        projectBroken.value = true
        buildProblems.value = err.fields?.problems ?? [{ message: err.message, line: err.fields?.line ?? null, section: null }]
      }
    }
    refreshIdentifierRegistry(projectId)
    refreshProjectFiles(projectId)
  }

  onBeforeUnmount(onProjectChanged((changedProjectId) => {
    if (changedProjectId === projectId) return refreshCatalog()
  }))

  return {
    validStateKeys, availableStates, projectBroken, buildProblems,
    stateLabelFor, actionLabelFor, refreshCatalog,
  }
}
