import {
  postAddState, postAddSignal, postAddEnvKey, postAddAction, putStateField, putProjectField,
  putActionField, putInitActionField, putSignalField, putEnvKeyField,
  deleteState, deleteProjectAction, deleteProjectSignal, deleteProjectEnvKey,
} from '../api.js'

// index.yml's own structural editing — add/edit/delete states, actions,
// signals, and env keys, without hand-writing YAML. Every call routes
// through `guardedAction` (useProjectFiles.js's own unsaved-changes guard)
// since none of this makes sense while the raw YAML buffer itself has
// unsaved edits. `selectedGraphElement`/`selectedStateKey` are the
// Inspector's own graph selection; `indexYmlEditorRef` resolves a
// state/action's current Graph element after index.yml's own text changes
// server-side.
export function useIndexYmlEditing(
  projectId, guardedAction, indexYmlEditorRef, jumpToDefinition, selectedGraphElement, selectedStateKey, flashRecentlyAdded
) {
  function handleAddState() {
    guardedAction('add a new state', async () => {
      try {
        const state = await postAddState(projectId)
        selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(state.key) ?? null
        flashRecentlyAdded(`state:${state.key}`)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleAddSignal() {
    guardedAction('add a new signal', async () => {
      try {
        const signal = await postAddSignal(projectId)
        flashRecentlyAdded(`signal:${signal.name}`)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleAddEnvKey() {
    guardedAction('add a new env key', async () => {
      try {
        const envKey = await postAddEnvKey(projectId)
        flashRecentlyAdded(`env-key:${envKey.name}`)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleAddAction() {
    const stateKey = selectedStateKey.value
    if (!stateKey) return
    guardedAction('add a new action', async () => {
      try {
        const action = await postAddAction(projectId, stateKey)
        // Selects the new action itself, not its containing state — selecting
        // the state would flip the Inspector's active tab back to "State" (see the selectedGraphElement watch above).
        selectedGraphElement.value = indexYmlEditorRef.value?.actionsForState(stateKey).find(
          (a) => a.data.actionName === action.name
        ) ?? null
        flashRecentlyAdded(`action:${stateKey}/${action.name}`)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleSetStateField(stateName, field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        await putStateField(projectId, stateName, field, value)
        selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(stateName) ?? null
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleSetProjectField(field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        await putProjectField(projectId, field, value)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleSetActionField(stateName, actionName, field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        // The init-action (stateName '') lives outside `states:` entirely,
        // so putActionField's state/action lookup can't reach it — its
        // fields go through the dedicated endpoint instead.
        if (stateName === '') {
          await putInitActionField(projectId, field, value)
        } else {
          await putActionField(projectId, stateName, actionName, field, value)
        }
        selectedGraphElement.value = indexYmlEditorRef.value?.actionsForState(stateName).find(
          (a) => a.data.actionName === actionName
        ) ?? null
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleSetSignalField(signalName, field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        const signal = await putSignalField(projectId, signalName, field, value)
        // Only a ui-label edit can rename the signal — its line in the YAML
        // moves, so re-jump to it off the *new* name the response reported.
        if (field === 'ui-label') await jumpToDefinition({ kind: 'signal', signalName: signal.name }, { silent: true })
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleSetEnvKeyField(envKeyName, field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        const envKey = await putEnvKeyField(projectId, envKeyName, field, value)
        // Only a 'name' edit can rename the key — its line in the YAML
        // moves, so re-jump to it off the *new* name the response reported.
        if (field === 'name') await jumpToDefinition({ kind: 'env-key', envKeyName: envKey.name }, { silent: true })
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleDeleteState(stateName) {
    guardedAction('delete this state', async () => {
      try {
        await deleteState(projectId, stateName)
        selectedGraphElement.value = null
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleDeleteAction(stateName, actionName) {
    guardedAction('delete this action', async () => {
      try {
        await deleteProjectAction(projectId, stateName, actionName)
        // The containing state is still selected — only the action itself
        // (if it happened to be the literal selection) is now gone.
        if (selectedGraphElement.value?.kind === 'action' && selectedGraphElement.value.data.actionName === actionName) {
          selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(stateName) ?? null
        }
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleDeleteSignal(signalName) {
    guardedAction('delete this signal', async () => {
      try {
        await deleteProjectSignal(projectId, signalName)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  function handleDeleteEnvKey(envKeyName) {
    guardedAction('delete this env key', async () => {
      try {
        await deleteProjectEnvKey(projectId, envKeyName)
      } catch {
        // already surfaced via apiFetch
      }
    })
  }

  return {
    handleAddState, handleAddSignal, handleAddEnvKey, handleAddAction,
    handleSetStateField, handleSetProjectField, handleSetActionField, handleSetSignalField, handleSetEnvKeyField,
    handleDeleteState, handleDeleteAction, handleDeleteSignal, handleDeleteEnvKey,
  }
}
