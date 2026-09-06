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

  async function loaded(sessionId = 7, isImported = false) {
    const s = mount(sessionId, isImported)
    await s.loadTimeline()
    return s
  }

  it('loadTimeline() loads messages, signals and the start state, refreshing the Inspector, and clears everything with no session', async () => {
    const empty = mount(null)
    await empty.loadTimeline()
    expect(empty.loading.value).toBe(false)
    expect(empty.rawMessages.value).toEqual([])
    expect(empty.signalsLog.value).toEqual([])
    expect(empty.sessionStartState.value).toBeNull()
    expect(getMessages).not.toHaveBeenCalled()
    empty.unmount?.()

    const s = await loaded()
    expect(getMessages).toHaveBeenCalledWith(7)
    expect(getSessionSignals).toHaveBeenCalledWith(7)
    expect(s.rawMessages.value).toEqual([MESSAGE_1, MESSAGE_2])
    expect(s.signalsLog.value).toEqual([signalsRow()])
    expect(s.sessionStartState.value).toBe('a')
    expect(s.loading.value).toBe(false)
    expect(inspectorRef.value.refresh).toHaveBeenCalled()

    getMessages.mockClear()
    s.currentSessionId.value = 42
    await vi.waitFor(() => expect(getMessages).toHaveBeenCalledWith(42))
  })

  it('selectMessage/selectTransition set `selected` to the right shape and resolve the row backing it', async () => {
    const s = await loaded()

    s.selectMessage(MESSAGE_1)
    expect(s.selected.value).toEqual({ kind: 'message', message: MESSAGE_1 })
    expect(s.annotatableSignalsRow.value).toEqual(signalsRow())
    expect(s.annotatableMessageId.value).toBe(1)

    const transition = signalsRow({ message_id: 5 })
    s.selectTransition(transition)
    expect(s.selected.value).toEqual({ kind: 'transition', transition })
    expect(s.annotatableMessageId.value).toBe(5)
  })

  it('a message with no evaluation row is unannotatable in a live session but gets a virtual placeholder in an imported one', async () => {
    getSessionSignals.mockResolvedValue([])

    const live = await loaded(7, false)
    live.selectMessage(MESSAGE_2)
    expect(live.annotatableSignalsRow.value).toBeNull()
    expect(live.annotatableMessageId.value).toBeNull()
    live.unmount?.()

    const imported = await loaded(7, true)
    imported.selectMessage(MESSAGE_2)
    expect(imported.annotatableSignalsRow.value).toMatchObject({ id: null, message_id: 2 })
    expect(imported.annotatableMessageId.value).toBe(2)
  })

  it('expectedState/expectedValues read off the backing row, and only the synthetic init row refuses expected signals', async () => {
    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'c', expected_values: '{"mood":80}' })])
    const annotated = await loaded()
    annotated.selectMessage(MESSAGE_1)
    expect(annotated.expectedState.value).toBe('c')
    expect(annotated.expectedValues.value).toEqual({ mood: 80 })
    expect(annotated.annotatableExpectedSignals.value).toBe(true)
    annotated.unmount?.()

    getSessionSignals.mockResolvedValue([signalsRow({ old_state: '' })])
    const init = await loaded()
    init.selectMessage(MESSAGE_1)
    expect(init.annotatableExpectedSignals.value).toBe(false)
  })

  it('onUpdateExpectedState/Signals write, reload the signals log and refresh the Inspector, doing nothing without a resolvable message id', async () => {
    const nothingSelected = await loaded()
    await nothingSelected.onUpdateExpectedState('z')
    await nothingSelected.onUpdateExpectedSignals({ a: 1 })
    expect(putMessageExpectedState).not.toHaveBeenCalled()
    expect(putMessageExpectedSignals).not.toHaveBeenCalled()
    nothingSelected.unmount?.()

    const s = await loaded()
    s.selectMessage(MESSAGE_1)
    inspectorRef.value.refresh.mockClear()
    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'z' })])

    await s.onUpdateExpectedState('z')
    expect(putMessageExpectedState).toHaveBeenCalledWith(1, 'z')
    expect(s.signalsLog.value).toEqual([signalsRow({ expected_state: 'z' })])
    expect(inspectorRef.value.refresh).toHaveBeenCalled()
    expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')

    await s.onUpdateExpectedSignals({ mood: 10 })
    expect(putMessageExpectedSignals).toHaveBeenCalledWith(1, { mood: 10 })
  })

  it('onSaveComment writes and reloads the signals log, but never touches the Inspector', async () => {
    const s = await loaded()
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
    const s = await loaded()
    s.selectTransition(s.signalsLog.value[0])

    getSessionSignals.mockResolvedValueOnce([signalsRow({ message_id: 2, action: 'new' })])
    await s.reloadSignalsLog()
    expect(s.selected.value.transition.action).toBe('new')

    getSessionSignals.mockResolvedValueOnce([]) // the transition disappeared entirely
    await s.reloadSignalsLog()
    expect(s.selected.value).toBeNull()
  })

  it('onUnlabelAll is a no-op with no session or nothing to unlabel, and otherwise confirms before deleting', async () => {
    const noSession = mount(null)
    await noSession.onUnlabelAll()
    expect(confirmDialog).not.toHaveBeenCalled()
    noSession.unmount?.()

    const unannotated = await loaded() // signalsRow() has no annotations
    expect(unannotated.hasAnyAnnotations.value).toBe(false)
    await unannotated.onUnlabelAll()
    expect(confirmDialog).not.toHaveBeenCalled()
    unannotated.unmount?.()

    getSessionSignals.mockResolvedValue([signalsRow({ expected_state: 'c' })])
    confirmDialog.mockResolvedValue(false)
    const declined = await loaded()
    expect(declined.hasAnyAnnotations.value).toBe(true)
    await declined.onUnlabelAll()
    expect(confirmDialog).toHaveBeenCalled()
    expect(deleteSessionAnnotations).not.toHaveBeenCalled()
    declined.unmount?.()

    confirmDialog.mockResolvedValue(true)
    const s = await loaded()
    await s.onUnlabelAll()
    expect(deleteSessionAnnotations).toHaveBeenCalledWith(7)
    expect(s.unlabelingAll.value).toBe(false)
  })
})
