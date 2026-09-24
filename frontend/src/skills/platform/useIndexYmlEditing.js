import {
  postAddState, postDuplicateState, postAddSignal, postAddEnvKey, postAddAction, putStateField, putProjectField, putServiceLevel,
  putActionField, putInitActionField, putSignalField, putEnvKeyField,
  deleteState, deleteProjectAction, deleteProjectSignal, deleteProjectEnvKey,
} from './api.js'

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
      }
    })
  }

  function handleDuplicateState(stateName) {
    guardedAction('duplicate this state', async () => {
      try {
        const state = await postDuplicateState(projectId, stateName)
        selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(state.key) ?? null
        flashRecentlyAdded(`state:${state.key}`)
      } catch {
      }
    })
  }

  function handleAddSignal() {
    guardedAction('add a new signal', async () => {
      try {
        const signal = await postAddSignal(projectId)
        flashRecentlyAdded(`signal:${signal.name}`)
      } catch {
      }
    })
  }

  function handleAddEnvKey() {
    guardedAction('add a new env key', async () => {
      try {
        const envKey = await postAddEnvKey(projectId)
        flashRecentlyAdded(`env-key:${envKey.name}`)
      } catch {
      }
    })
  }

  function handleAddAction() {
    const stateKey = selectedStateKey.value
    if (!stateKey) return
    guardedAction('add a new action', async () => {
      try {
        const action = await postAddAction(projectId, stateKey)
        selectedGraphElement.value = indexYmlEditorRef.value?.actionsForState(stateKey).find(
          (a) => a.data.actionName === action.name
        ) ?? null
        flashRecentlyAdded(`action:${stateKey}/${action.name}`)
      } catch {
      }
    })
  }

  function handleSetStateField(stateName, field, value) {
    return guardedAction(`edit "${field}"`, async () => {
      try {
        await putStateField(projectId, stateName, field, value)
        return true
      } catch {
        return false
      }
    })
  }

  function handleSetProjectField(field, value) {
    return guardedAction(`edit "${field}"`, async () => {
      try {
        return await putProjectField(projectId, field, value)
      } catch {
        return undefined
      }
    })
  }

  function handleSetServiceLevel(service, level) {
    guardedAction(`set "${service}" to ${level}`, async () => {
      try {
        await putServiceLevel(projectId, service, level)
      } catch {
      }
    })
  }

  function handleSetActionField(stateName, actionName, field, value) {
    return guardedAction(`edit "${field}"`, async () => {
      try {
        if (stateName === '') {
          await putInitActionField(projectId, field, value)
        } else {
          await putActionField(projectId, stateName, actionName, field, value)
        }
        selectedGraphElement.value = indexYmlEditorRef.value?.actionsForState(stateName).find(
          (a) => a.data.actionName === actionName
        ) ?? null
        return true
      } catch {
        return false
      }
    })
  }

  function handleSetSignalField(signalName, field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        const signal = await putSignalField(projectId, signalName, field, value)
        if (field === 'ui-label') await jumpToDefinition({ kind: 'signal', signalName: signal.name }, { silent: true })
      } catch {
      }
    })
  }

  function handleSetEnvKeyField(envKeyName, field, value) {
    guardedAction(`edit "${field}"`, async () => {
      try {
        const envKey = await putEnvKeyField(projectId, envKeyName, field, value)
        if (field === 'name') await jumpToDefinition({ kind: 'env-key', envKeyName: envKey.name }, { silent: true })
      } catch {
      }
    })
  }

  function handleDeleteState(stateName) {
    guardedAction('delete this state', async () => {
      try {
        await deleteState(projectId, stateName, () => { selectedGraphElement.value = null })
      } catch {
      }
    })
  }

  function handleDeleteAction(stateName, actionName) {
    guardedAction('delete this action', async () => {
      try {
        await deleteProjectAction(projectId, stateName, actionName)
        if (selectedGraphElement.value?.kind === 'action' && selectedGraphElement.value.data.actionName === actionName) {
          selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(stateName) ?? null
        }
      } catch {
      }
    })
  }

  function handleDeleteSignal(signalName) {
    return guardedAction('delete this signal', async () => {
      try {
        await deleteProjectSignal(projectId, signalName)
        return true
      } catch {
        return false
      }
    })
  }

  function handleDeleteEnvKey(envKeyName) {
    guardedAction('delete this env key', async () => {
      try {
        await deleteProjectEnvKey(projectId, envKeyName)
      } catch {
      }
    })
  }

  return {
    handleAddState, handleDuplicateState, handleAddSignal, handleAddEnvKey, handleAddAction,
    handleSetStateField, handleSetProjectField, handleSetServiceLevel, handleSetActionField, handleSetSignalField, handleSetEnvKeyField,
    handleDeleteState, handleDeleteAction, handleDeleteSignal, handleDeleteEnvKey,
  }
}
