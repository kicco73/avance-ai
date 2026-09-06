import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('../src/api.js', () => ({
  postAddState: vi.fn(),
  postAddSignal: vi.fn(),
  postAddEnvKey: vi.fn(),
  postAddAction: vi.fn(),
  putStateField: vi.fn(),
  putProjectField: vi.fn(),
  putActionField: vi.fn(),
  putInitActionField: vi.fn(),
  putSignalField: vi.fn(),
  putEnvKeyField: vi.fn(),
  putActionOrder: vi.fn(),
  deleteState: vi.fn(),
  deleteProjectAction: vi.fn(),
  deleteProjectSignal: vi.fn(),
  deleteProjectEnvKey: vi.fn(),
}))

import {
  postAddState, postAddSignal, postAddEnvKey, postAddAction, putStateField, putProjectField,
  putActionField, putInitActionField, putSignalField, putEnvKeyField,
  deleteState, deleteProjectAction, deleteProjectSignal, deleteProjectEnvKey,
} from '../src/api.js'
import { useIndexYmlEditing } from '../src/composables/useIndexYmlEditing.js'

describe('useIndexYmlEditing', () => {
  let selectedGraphElement, selectedStateKey, indexYmlEditorRef, flashRecentlyAdded, jumpToDefinition, s

  beforeEach(() => {
    vi.clearAllMocks()
    selectedGraphElement = ref(null)
    selectedStateKey = ref(null)
    indexYmlEditorRef = ref({
      stateElementFor: vi.fn((key) => ({ kind: 'state', data: { id: key } })),
      actionsForState: vi.fn(() => []),
    })
    flashRecentlyAdded = vi.fn()
    jumpToDefinition = vi.fn()
    // Simulates a never-dirty editor: guardedAction just runs immediately,
    // matching useProjectFiles.js's own (separately tested) not-dirty branch.
    const guardedAction = (label, run) => run()
    s = useIndexYmlEditing(
      'proj', guardedAction, indexYmlEditorRef, jumpToDefinition, selectedGraphElement, selectedStateKey, flashRecentlyAdded
    )
  })

  it('every add flashes what it created, selecting only the new state or action, never a signal or env key', async () => {
    postAddState.mockResolvedValue({ key: 'newState' })
    await s.handleAddState()
    expect(postAddState).toHaveBeenCalledWith('proj')
    expect(selectedGraphElement.value).toEqual({ kind: 'state', data: { id: 'newState' } })
    expect(flashRecentlyAdded).toHaveBeenCalledWith('state:newState')

    selectedGraphElement.value = null
    postAddSignal.mockResolvedValue({ name: 'newSignal' })
    await s.handleAddSignal()
    expect(flashRecentlyAdded).toHaveBeenCalledWith('signal:newSignal')
    expect(selectedGraphElement.value).toBeNull()

    postAddEnvKey.mockResolvedValue({ name: 'newKey' })
    await s.handleAddEnvKey()
    expect(flashRecentlyAdded).toHaveBeenCalledWith('env-key:newKey')
    expect(selectedGraphElement.value).toBeNull()
  })

  it('handleAddAction needs a selected state, then selects the new action itself rather than its containing state', async () => {
    await s.handleAddAction()
    expect(postAddAction).not.toHaveBeenCalled()

    selectedStateKey.value = 'greeting'
    postAddAction.mockResolvedValue({ name: 'go' })
    const actionEl = { kind: 'action', data: { actionName: 'go' } }
    indexYmlEditorRef.value.actionsForState.mockReturnValue([{ kind: 'action', data: { actionName: 'other' } }, actionEl])

    await s.handleAddAction()

    expect(postAddAction).toHaveBeenCalledWith('proj', 'greeting')
    expect(selectedGraphElement.value).toEqual(actionEl)
    expect(flashRecentlyAdded).toHaveBeenCalledWith('action:greeting/go')
  })

  it('handleSetStateField re-resolves the graph selection while handleSetProjectField has no selection side effect', async () => {
    await s.handleSetStateField('greeting', 'ui-label', 'Hi there')
    expect(putStateField).toHaveBeenCalledWith('proj', 'greeting', 'ui-label', 'Hi there')
    expect(selectedGraphElement.value).toEqual({ kind: 'state', data: { id: 'greeting' } })

    selectedGraphElement.value = null
    await s.handleSetProjectField('id', 'my-project')
    expect(putProjectField).toHaveBeenCalledWith('proj', 'id', 'my-project')
    expect(selectedGraphElement.value).toBeNull()
  })

  describe('handleSetActionField', () => {
    it('routes the init-action (stateName "") through putInitActionField, any other action through putActionField', async () => {
      const actionEl = { kind: 'action', data: { actionName: 'go' } }
      indexYmlEditorRef.value.actionsForState.mockReturnValue([actionEl])

      await s.handleSetActionField('', 'start', 'target', 'greeting')
      expect(putInitActionField).toHaveBeenCalledWith('proj', 'target', 'greeting')
      expect(putActionField).not.toHaveBeenCalled()

      await s.handleSetActionField('greeting', 'go', 'target', 'farewell')
      expect(putActionField).toHaveBeenCalledWith('proj', 'greeting', 'go', 'target', 'farewell')
      expect(selectedGraphElement.value).toEqual(actionEl)
    })

    // OnEnterDialog.vue's own OK button awaits exactly this return value
    // (through EditProjectView.vue's handleSetSelectedElementField) to
    // decide whether to close — it must never resolve true for a write
    // that never actually landed, and never throw.
    it('resolves true once the write succeeds and false when it is rejected', async () => {
      putActionField.mockResolvedValue({})
      await expect(s.handleSetActionField('greeting', 'go', 'on-enter', 'actuator.celebrate()')).resolves.toBe(true)

      putActionField.mockRejectedValue(new Error('invalid on-enter'))
      await expect(s.handleSetActionField('greeting', 'go', 'on-enter', 'not.a.real.call()')).resolves.toBe(false)
    })
  })

  it('a signal ui-label or an env key name edit jumps to the possibly-renamed definition, no other field does', async () => {
    putSignalField.mockResolvedValue({ name: 'renamedSignal' })
    await s.handleSetSignalField('oldName', 'ui-label', 'renamedSignal')
    expect(jumpToDefinition).toHaveBeenCalledWith({ kind: 'signal', signalName: 'renamedSignal' }, { silent: true })

    putEnvKeyField.mockResolvedValue({ name: 'renamedKey' })
    await s.handleSetEnvKeyField('oldKey', 'name', 'renamedKey')
    expect(jumpToDefinition).toHaveBeenCalledWith({ kind: 'env-key', envKeyName: 'renamedKey' }, { silent: true })

    jumpToDefinition.mockClear()
    putSignalField.mockResolvedValue({ name: 'sig' })
    await s.handleSetSignalField('sig', 'definition', 'signal.mood >= 50')
    putEnvKeyField.mockResolvedValue({ name: 'key' })
    await s.handleSetEnvKeyField('key', 'value', '42')
    expect(jumpToDefinition).not.toHaveBeenCalled()
  })

  it('deleting a state clears the selection outright, while deleting a signal or env key never touches it', async () => {
    selectedGraphElement.value = { kind: 'action', data: { actionName: 'go' } }

    await s.handleDeleteState('greeting')
    expect(deleteState).toHaveBeenCalledWith('proj', 'greeting')
    expect(selectedGraphElement.value).toBeNull()

    const selection = { kind: 'state', data: { id: 'x' } }
    selectedGraphElement.value = selection
    await s.handleDeleteSignal('sig')
    await s.handleDeleteEnvKey('key')
    expect(deleteProjectSignal).toHaveBeenCalledWith('proj', 'sig')
    expect(deleteProjectEnvKey).toHaveBeenCalledWith('proj', 'key')
    expect(selectedGraphElement.value).toEqual(selection)
  })

  it('deleting an action falls back to its containing state only when that exact action was selected', async () => {
    selectedGraphElement.value = { kind: 'action', data: { actionName: 'go' } }
    await s.handleDeleteAction('greeting', 'go')
    expect(deleteProjectAction).toHaveBeenCalledWith('proj', 'greeting', 'go')
    expect(selectedGraphElement.value).toEqual({ kind: 'state', data: { id: 'greeting' } })

    const stateSelection = { kind: 'state', data: { id: 'greeting' } }
    selectedGraphElement.value = stateSelection
    await s.handleDeleteAction('greeting', 'go')
    expect(selectedGraphElement.value).toEqual(stateSelection)

    const otherAction = { kind: 'action', data: { actionName: 'other' } }
    selectedGraphElement.value = otherAction
    await s.handleDeleteAction('greeting', 'go')
    expect(selectedGraphElement.value).toEqual(otherAction)
  })
})
