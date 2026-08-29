import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  getMessages: vi.fn(),
  getSessionSignals: vi.fn(),
  getSessions: vi.fn(),
  putMessageExpectedState: vi.fn(),
  putMessageExpectedSignals: vi.fn(),
  putMessageComment: vi.fn(),
  deleteSessionAnnotations: vi.fn(),
}))
vi.mock('../src/chatStore.js', () => ({
  refreshSessionsQuietly: vi.fn(),
}))
vi.mock('../src/dialogStore.js', () => ({
  confirmDialog: vi.fn(),
}))

import {
  getMessages, getSessionSignals, getSessions, putMessageExpectedState, putMessageExpectedSignals,
  putMessageComment, deleteSessionAnnotations,
} from '../src/api.js'
import { refreshSessionsQuietly } from '../src/chatStore.js'
import { confirmDialog } from '../src/dialogStore.js'
import { useSessionAnnotation } from '../src/composables/useSessionAnnotation.js'

// onBeforeUnmount/watch need an active component instance to be reactive
// in the way the real component relies on.
function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

const MESSAGE_1 = { id: 1, timestamp: '2026-01-01T00:00:00Z', text: 'hi' }
const MESSAGE_2 = { id: 2, timestamp: '2026-01-01T00:01:00Z', text: 'bye' }

function signalsRow(overrides = {}) {
  return {
    id: 10, message_id: 1, timestamp: '2026-01-01T00:00:00Z', values: null,
    expected_values: null, old_state: 'a', new_state: 'b', action: 'go',
    expected_state: null, comment: null, session_id: 1,
    ...overrides,
  }
}

describe('useSessionAnnotation', () => {
  let unmount
  const inspectorRef = ref({ refresh: vi.fn() })

  beforeEach(() => {
    vi.clearAllMocks()
    inspectorRef.value = { refresh: vi.fn() }
    getMessages.mockResolvedValue([MESSAGE_1, MESSAGE_2])
    getSessionSignals.mockResolvedValue([signalsRow()])
    getSessions.mockResolvedValue([{ id: 7, start_state: 'a' }])
  })

  afterEach(() => {
    unmount?.()
  })

  function mount(sessionId = 7, isImported = false) {
    const currentSessionId = ref(sessionId)
    const currentSessionIsImported = ref(isImported)
    const mounted = mountComposable(() =>
      useSessionAnnotation('proj', currentSessionId, currentSessionIsImported, inspectorRef)
    )
    unmount = mounted.unmount
    return { ...mounted.result, currentSessionId, currentSessionIsImported }
  }

  it('loadTimeline() with no session clears state and never calls the API', async () => {
    const s = mount(null)
    await s.loadTimeline()

    expect(s.loading.value).toBe(false)
    expect(s.rawMessages.value).toEqual([])
    expect(s.signalsLog.value).toEqual([])
    expect(s.sessionStartState.value).toBeNull()
    expect(getMessages).not.toHaveBeenCalled()
  })

  it('loadTimeline() with a session loads messages/signals/start-state and refreshes the Inspector', async () => {
    const s = mount(7)
    await s.loadTimeline()

    expect(getMessages).toHaveBeenCalledWith(7)
    expect(getSessionSignals).toHaveBeenCalledWith(7)
    expect(s.rawMessages.value).toEqual([MESSAGE_1, MESSAGE_2])
    expect(s.signalsLog.value).toEqual([signalsRow()])
    expect(s.sessionStartState.value).toBe('a')
    expect(s.loading.value).toBe(false)
    expect(inspectorRef.value.refresh).toHaveBeenCalled()
  })

  it('switching currentSessionId triggers loadTimeline on its own (the watch)', async () => {
    const s = mount(7)
    await s.loadTimeline()
    getMessages.mockClear()

    s.currentSessionId.value = 42
    await vi.waitFor(() => expect(getMessages).toHaveBeenCalledWith(42))
  })

  it('selectMessage/selectTransition set `selected` to the right shape', () => {
    const s = mount(7)
    s.selectMessage(MESSAGE_1)
    expect(s.selected.value).toEqual({ kind: 'message', message: MESSAGE_1 })

    const transition = { message_id: 1, old_state: 'a', new_state: 'b' }
    s.selectTransition(transition)
    expect(s.selected.value).toEqual({ kind: 'transition', transition })
  })

  it('annotatableSignalsRow finds the row backing a selected message', async () => {
    const s = mount(7)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_1)

    expect(s.annotatableSignalsRow.value).toEqual(signalsRow())
    expect(s.annotatableMessageId.value).toBe(1)
  })

  it('annotatableSignalsRow is null for a live message with no evaluation row', async () => {
    getSessionSignals.mockResolvedValue([])
    const s = mount(7, false)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_2)

    expect(s.annotatableSignalsRow.value).toBeNull()
    expect(s.annotatableMessageId.value).toBeNull()
  })

  it('annotatableSignalsRow falls back to a virtual placeholder for an imported session', async () => {
    getSessionSignals.mockResolvedValue([])
    const s = mount(7, true)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_2)

    expect(s.annotatableSignalsRow.value).toMatchObject({ id: null, message_id: 2 })
    expect(s.annotatableMessageId.value).toBe(2)
  })

  it('a transition selection resolves annotatableMessageId off the transition row itself', async () => {
    const s = mount(7)
    await s.loadTimeline()
    s.selectTransition(signalsRow({ message_id: 5 }))

    expect(s.annotatableMessageId.value).toBe(5)
  })

  it('expectedState/expectedValues read off annotatableSignalsRow, parsing expected_values JSON', async () => {
    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'c', expected_values: '{"mood":80}' })])
    const s = mount(7)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_1)

    expect(s.expectedState.value).toBe('c')
    expect(s.expectedValues.value).toEqual({ mood: 80 })
  })

  it("annotatableExpectedSignals is false only for the synthetic init row (old_state === '')", async () => {
    getSessionSignals.mockResolvedValue([signalsRow({ old_state: '' })])
    const s = mount(7)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_1)

    expect(s.annotatableExpectedSignals.value).toBe(false)
  })

  it('onUpdateExpectedState writes, reloads signals, and refreshes the Inspector', async () => {
    const s = mount(7)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_1)
    inspectorRef.value.refresh.mockClear()
    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'z' })])

    await s.onUpdateExpectedState('z')

    expect(putMessageExpectedState).toHaveBeenCalledWith(1, 'z')
    expect(s.signalsLog.value).toEqual([signalsRow({ expected_state: 'z' })])
    expect(inspectorRef.value.refresh).toHaveBeenCalled()
    expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
  })

  it('onUpdateExpectedSignals writes, reloads signals, and refreshes the Inspector', async () => {
    const s = mount(7)
    await s.loadTimeline()
    s.selectMessage(MESSAGE_1)

    await s.onUpdateExpectedSignals({ mood: 10 })

    expect(putMessageExpectedSignals).toHaveBeenCalledWith(1, { mood: 10 })
  })

  it('onUpdateExpectedState/Signals do nothing without a resolvable message id', async () => {
    const s = mount(7)
    await s.loadTimeline() // nothing selected

    await s.onUpdateExpectedState('z')
    await s.onUpdateExpectedSignals({ a: 1 })

    expect(putMessageExpectedState).not.toHaveBeenCalled()
    expect(putMessageExpectedSignals).not.toHaveBeenCalled()
  })

  it('onSaveComment writes and reloads signals, but does not touch the Inspector', async () => {
    const s = mount(7)
    await s.loadTimeline()
    inspectorRef.value.refresh.mockClear()

    await s.onSaveComment(1, 'a note')

    expect(putMessageComment).toHaveBeenCalledWith(1, 'a note')
    expect(getSessionSignals).toHaveBeenCalledTimes(2) // initial load + reload
    expect(inspectorRef.value.refresh).not.toHaveBeenCalled()
  })

  it('reloadSignalsLog re-points a selected transition at its fresh copy by message_id, or clears it if gone', async () => {
    // message_id: 2 (not the first message) so buildTimeline's synthetic
    // session-start entry — tied to the *first* message's id — never
    // collides with this transition's own message_id.
    getSessionSignals.mockResolvedValueOnce([signalsRow({ message_id: 2, action: 'old' })])
    const s = mount(7)
    await s.loadTimeline()
    s.selectTransition(s.signalsLog.value[0])

    getSessionSignals.mockResolvedValueOnce([signalsRow({ message_id: 2, action: 'new' })])
    await s.reloadSignalsLog()
    expect(s.selected.value.transition.action).toBe('new')

    getSessionSignals.mockResolvedValueOnce([]) // the transition disappeared entirely
    await s.reloadSignalsLog()
    expect(s.selected.value).toBeNull()
  })

  it('hasAnyAnnotations is true iff some row has an expected_state or expected_values', async () => {
    getSessionSignals.mockResolvedValue([signalsRow()])
    const s = mount(7)
    await s.loadTimeline()
    expect(s.hasAnyAnnotations.value).toBe(false)

    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'c' })])
    await s.loadTimeline()
    expect(s.hasAnyAnnotations.value).toBe(true)
  })

  it('onUnlabelAll is a no-op without a session or without anything to unlabel', async () => {
    const s = mount(null)
    await s.onUnlabelAll()
    expect(confirmDialog).not.toHaveBeenCalled()

    const s2 = mount(7)
    await s2.loadTimeline() // signalsRow() has no annotations
    await s2.onUnlabelAll()
    expect(confirmDialog).not.toHaveBeenCalled()
  })

  it('onUnlabelAll confirms, deletes, reloads, and leaves unlabelingAll false once settled', async () => {
    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'c' })])
    confirmDialog.mockResolvedValue(true)
    const s = mount(7)
    await s.loadTimeline()

    await s.onUnlabelAll()

    expect(confirmDialog).toHaveBeenCalled()
    expect(deleteSessionAnnotations).toHaveBeenCalledWith(7)
    expect(s.unlabelingAll.value).toBe(false)
  })

  it('onUnlabelAll does nothing if the confirm dialog is declined', async () => {
    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'c' })])
    confirmDialog.mockResolvedValue(false)
    const s = mount(7)
    await s.loadTimeline()

    await s.onUnlabelAll()

    expect(deleteSessionAnnotations).not.toHaveBeenCalled()
  })
})
