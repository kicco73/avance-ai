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
  putActionField, putInitActionField, putSignalField, putEnvKeyField, putActionOrder,
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

  it('handleAddState selects the new state and flashes it', async () => {
    postAddState.mockResolvedValue({ key: 'newState' })

    await s.handleAddState()

    expect(postAddState).toHaveBeenCalledWith('proj')
    expect(selectedGraphElement.value).toEqual({ kind: 'state', data: { id: 'newState' } })
    expect(flashRecentlyAdded).toHaveBeenCalledWith('state:newState')
  })

  it('handleAddSignal flashes the new signal without touching selection', async () => {
    postAddSignal.mockResolvedValue({ name: 'newSignal' })

    await s.handleAddSignal()

    expect(flashRecentlyAdded).toHaveBeenCalledWith('signal:newSignal')
    expect(selectedGraphElement.value).toBeNull()
  })

  it('handleAddEnvKey flashes the new env key', async () => {
    postAddEnvKey.mockResolvedValue({ name: 'newKey' })

    await s.handleAddEnvKey()

    expect(flashRecentlyAdded).toHaveBeenCalledWith('env-key:newKey')
  })

  describe('handleAddAction', () => {
    it('does nothing without a selected state', async () => {
      selectedStateKey.value = null
      await s.handleAddAction()
      expect(postAddAction).not.toHaveBeenCalled()
    })

    it('selects the new action (not its containing state) and flashes it', async () => {
      selectedStateKey.value = 'greeting'
      postAddAction.mockResolvedValue({ name: 'go' })
      const actionEl = { kind: 'action', data: { actionName: 'go' } }
      indexYmlEditorRef.value.actionsForState.mockReturnValue([{ kind: 'action', data: { actionName: 'other' } }, actionEl])

      await s.handleAddAction()

      expect(postAddAction).toHaveBeenCalledWith('proj', 'greeting')
      expect(selectedGraphElement.value).toEqual(actionEl)
      expect(flashRecentlyAdded).toHaveBeenCalledWith('action:greeting/go')
    })
  })

  it('handleSetStateField writes the field and re-resolves the graph selection', async () => {
    await s.handleSetStateField('greeting', 'ui-label', 'Hi there')

    expect(putStateField).toHaveBeenCalledWith('proj', 'greeting', 'ui-label', 'Hi there')
    expect(selectedGraphElement.value).toEqual({ kind: 'state', data: { id: 'greeting' } })
  })

  it('handleSetProjectField just writes the field, no selection side effect', async () => {
    await s.handleSetProjectField('id', 'my-project')

    expect(putProjectField).toHaveBeenCalledWith('proj', 'id', 'my-project')
    expect(selectedGraphElement.value).toBeNull()
  })

  describe('handleSetActionField', () => {
    it('routes the init-action (stateName "") through putInitActionField instead of putActionField', async () => {
      indexYmlEditorRef.value.actionsForState.mockReturnValue([{ kind: 'action', data: { actionName: 'start' } }])

      await s.handleSetActionField('', 'start', 'target', 'greeting')

      expect(putInitActionField).toHaveBeenCalledWith('proj', 'target', 'greeting')
      expect(putActionField).not.toHaveBeenCalled()
    })

    it('routes a normal action through putActionField and re-resolves its element', async () => {
      const actionEl = { kind: 'action', data: { actionName: 'go' } }
      indexYmlEditorRef.value.actionsForState.mockReturnValue([actionEl])

      await s.handleSetActionField('greeting', 'go', 'target', 'farewell')

      expect(putActionField).toHaveBeenCalledWith('proj', 'greeting', 'go', 'target', 'farewell')
      expect(selectedGraphElement.value).toEqual(actionEl)
    })

    // OnEnterDialog.vue's own OK button awaits exactly this return value
    // (through EditProjectView.vue's handleSetSelectedElementField) to
    // decide whether to close — it must never resolve true for a write
    // that never actually landed.
    it('resolves true once the write succeeds', async () => {
      putActionField.mockResolvedValue({})

      await expect(s.handleSetActionField('greeting', 'go', 'on-enter', 'actuator.celebrate()')).resolves.toBe(true)
    })

    it('resolves false (never throws) when the write is rejected — e.g. a malformed on-enter script', async () => {
      putActionField.mockRejectedValue(new Error('invalid on-enter'))

      await expect(s.handleSetActionField('greeting', 'go', 'on-enter', 'not.a.real.call()')).resolves.toBe(false)
    })
  })

  describe('handleSetSignalField', () => {
    it('jumps to the (possibly renamed) definition only on a ui-label edit', async () => {
      putSignalField.mockResolvedValue({ name: 'renamedSignal' })

      await s.handleSetSignalField('oldName', 'ui-label', 'renamedSignal')

      expect(jumpToDefinition).toHaveBeenCalledWith({ kind: 'signal', signalName: 'renamedSignal' }, { silent: true })
    })

    it('does not jump for any other field', async () => {
      putSignalField.mockResolvedValue({ name: 'sig' })

      await s.handleSetSignalField('sig', 'definition', 'signal.mood >= 50')

      expect(jumpToDefinition).not.toHaveBeenCalled()
    })
  })

  describe('handleSetEnvKeyField', () => {
    it('jumps to the (possibly renamed) definition only on a name edit', async () => {
      putEnvKeyField.mockResolvedValue({ name: 'renamedKey' })

      await s.handleSetEnvKeyField('oldKey', 'name', 'renamedKey')

      expect(jumpToDefinition).toHaveBeenCalledWith({ kind: 'env-key', envKeyName: 'renamedKey' }, { silent: true })
    })

    it('does not jump for any other field', async () => {
      putEnvKeyField.mockResolvedValue({ name: 'key' })

      await s.handleSetEnvKeyField('key', 'value', '42')

      expect(jumpToDefinition).not.toHaveBeenCalled()
    })
  })

  it('handleDeleteState deletes and clears the graph selection unconditionally', async () => {
    selectedGraphElement.value = { kind: 'action', data: { actionName: 'go' } }

    await s.handleDeleteState('greeting')

    expect(deleteState).toHaveBeenCalledWith('proj', 'greeting')
    expect(selectedGraphElement.value).toBeNull()
  })

  describe('handleDeleteAction', () => {
    it('falls back to the containing state when the deleted action was the exact selection', async () => {
      selectedGraphElement.value = { kind: 'action', data: { actionName: 'go' } }

      await s.handleDeleteAction('greeting', 'go')

      expect(deleteProjectAction).toHaveBeenCalledWith('proj', 'greeting', 'go')
      expect(selectedGraphElement.value).toEqual({ kind: 'state', data: { id: 'greeting' } })
    })

    it('leaves an unrelated selection alone', async () => {
      const stateSelection = { kind: 'state', data: { id: 'greeting' } }
      selectedGraphElement.value = stateSelection

      await s.handleDeleteAction('greeting', 'go')

      expect(selectedGraphElement.value).toEqual(stateSelection)
    })

    it('leaves a different action selection alone', async () => {
      const otherAction = { kind: 'action', data: { actionName: 'other' } }
      selectedGraphElement.value = otherAction

      await s.handleDeleteAction('greeting', 'go')

      expect(selectedGraphElement.value).toEqual(otherAction)
    })
  })

  it('handleDeleteSignal/handleDeleteEnvKey call through without touching selection', async () => {
    const selection = { kind: 'state', data: { id: 'x' } }
    selectedGraphElement.value = selection

    await s.handleDeleteSignal('sig')
    await s.handleDeleteEnvKey('key')

    expect(deleteProjectSignal).toHaveBeenCalledWith('proj', 'sig')
    expect(deleteProjectEnvKey).toHaveBeenCalledWith('proj', 'key')
    expect(selectedGraphElement.value).toEqual(selection)
  })

  // handleReorderAction no longer lives on this composable — action
  // reordering (putActionOrder) moved into ActionsOrderDialog.vue, which
  // owns selectedStateKey/props.stateName itself and calls the API
  // directly (see its own template). Skipped rather than deleted so the
  // missing coverage stays visible: no dedicated test file for
  // ActionsOrderDialog.vue exists yet to have inherited these two cases.
  describe.skip('handleReorderAction (moved to ActionsOrderDialog.vue)', () => {
    it('does nothing without a selected state', async () => {
      selectedStateKey.value = null
      await s.handleReorderAction({ actionName: 'go', position: 2 })
      expect(putActionOrder).not.toHaveBeenCalled()
    })

    it('reorders within the currently selected state', async () => {
      selectedStateKey.value = 'greeting'

      await s.handleReorderAction({ actionName: 'go', position: 2 })

      expect(putActionOrder).toHaveBeenCalledWith('proj', 'greeting', 'go', 2)
    })
  })
})
