import { ref } from 'vue'
import { getProjectGraph } from '../api.js'
import { clearApiError, setApiWarning } from '../errorStore.js'
import { refreshIdentifierRegistry } from '../identifierRegistry.js'
import { refreshProjectFiles } from '../projectFiles.js'

// The draft's automaton-derived catalog: state keys/labels, action labels
// (keyed `${stateKey}::${actionName}`, names are only unique per state),
// build warnings and the "project_broken" flag.
export function useProjectCatalog(projectId) {
  const validStateKeys = ref(new Set())
  const availableStates = ref([])
  const projectBroken = ref(false)
  const buildWarnings = ref([])
  const actionLabelsByState = ref(new Map())

  function stateLabelFor(key) {
    return availableStates.value.find((s) => s.key === key)?.uiLabel ?? key
  }

  function actionLabelFor(stateKey, actionName) {
    return actionLabelsByState.value.get(`${stateKey}::${actionName}`) ?? actionName
  }

  async function refreshCatalog() {
    try {
      const { nodes, edges, build_warnings } = await getProjectGraph(projectId)
      validStateKeys.value = new Set(nodes.map((n) => n.state.key))
      availableStates.value = nodes.map((n) => ({ key: n.state.key, uiLabel: n.state.ui_label }))
      actionLabelsByState.value = new Map(edges.map((e) => [`${e.source}::${e.action.name}`, e.action.ui_label]))
      buildWarnings.value = build_warnings || []
      if (projectBroken.value) {
        projectBroken.value = false
        clearApiError()
      }
    } catch (err) {
      if (err?.code === 'project_broken') {
        projectBroken.value = true
        buildWarnings.value = []
        setApiWarning(
          `Project '${projectId}' is broken — its stored index.yml no longer builds. Fix it below using the file editor.`,
          err.message
        )
      }
    }
    refreshIdentifierRegistry(projectId)
    refreshProjectFiles(projectId)
  }

  return {
    validStateKeys, availableStates, projectBroken, buildWarnings,
    stateLabelFor, actionLabelFor, refreshCatalog,
  }
}
