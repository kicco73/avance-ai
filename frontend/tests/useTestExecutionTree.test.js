import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  createTestEventsSource: vi.fn(),
  deleteTestJob: vi.fn(),
  deleteTests: vi.fn(),
  getAggregateResult: vi.fn(),
  getTests: vi.fn(),
  getJobsStatus: vi.fn(),
  postTest: vi.fn(),
  postRootAggregation: vi.fn(),
  postSessionsRun: vi.fn(),
  postSignalTest: vi.fn(),
  postSignalsAggregation: vi.fn(),
  postStateTest: vi.fn(),
  postStatesAggregation: vi.fn(),
  postUserSessionsRun: vi.fn(),
  postUsersAggregation: vi.fn(),
}))
vi.mock('../src/dialogStore.js', () => ({
  confirmDialog: vi.fn(),
}))

import {
  createTestEventsSource, deleteTestJob, deleteTests, getAggregateResult, getTests, getJobsStatus,
  postTest, postRootAggregation, postSessionsRun, postSignalTest, postSignalsAggregation,
  postStateTest, postStatesAggregation, postUserSessionsRun, postUsersAggregation,
} from '../src/api.js'
import { confirmDialog } from '../src/dialogStore.js'
import { useTestExecutionTree } from '../src/composables/useTestExecutionTree.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

function fakeEventSource() {
  return { onmessage: null, close: vi.fn() }
}

describe('useTestExecutionTree', () => {
  let unmount, emit, strategy, sessions, projectSignals

  beforeEach(() => {
    vi.clearAllMocks()
    createTestEventsSource.mockReturnValue(fakeEventSource())
    getJobsStatus.mockResolvedValue({ sessions: [], aggregates: [] })
    emit = vi.fn()
    strategy = ref('batch')
    sessions = ref([{ id: 1, title: 'My session' }])
    projectSignals = ref([{ name: 'mood', ui_label: 'Mood' }])
  })

  afterEach(() => {
    unmount?.()
  })

  function mount() {
    const mounted = mountComposable(() => useTestExecutionTree('proj', strategy, sessions, projectSignals, emit))
    unmount = mounted.unmount
    return mounted.result
  }

  describe('onMounted wiring', () => {
    it('selects root, hydrates job status, and connects the SSE source', () => {
      const s = mount()

      expect(s.selectedNodeId.value).toBe('root')
      expect(emit).toHaveBeenCalledWith('select', 'root')
      expect(getJobsStatus).toHaveBeenCalledWith('proj', 'batch')
      expect(createTestEventsSource).toHaveBeenCalledWith('proj')
    })

    it('routes incoming SSE messages through handleTestEvent', () => {
      const es = fakeEventSource()
      createTestEventsSource.mockReturnValue(es)
      const s = mount()

      es.onmessage({ data: JSON.stringify({ key: 'batch:state:greeting', job_status: 'running', queue_status: 'running' }) })

      expect(s.currentStrategyStatuses.value['state:greeting']).toBe('running')
    })

    it('closes the event source on unmount', () => {
      const es = fakeEventSource()
      createTestEventsSource.mockReturnValue(es)
      const mounted = mountComposable(() => useTestExecutionTree('proj', strategy, sessions, projectSignals, emit))
      mounted.unmount()
      expect(es.close).toHaveBeenCalled()
    })
  })

  describe('currentStrategyStatuses / currentStrategyProgress', () => {
    it('only surfaces events for the active strategy, keyed by bare nodeId', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:state:a', job_status: 'running', queue_status: 'running', percentage: 40 })
      s.handleTestEvent({ key: 'turn_by_turn:state:a', job_status: 'completed', queue_status: 'exited' })

      // 'root' isn't in the map at all until a real event names it — its
      // 'idle' default comes from rootStatus's own ?? fallback, not this map.
      expect(s.currentStrategyStatuses.value).toEqual({ 'state:a': 'running' })
      expect(s.currentStrategyProgress.value).toEqual({ 'state:a': 40 })
    })

    it('an exited+failed job with an error message maps to "warning" only when completed, "fail" when failed', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:state:a', job_status: 'completed', queue_status: 'exited', error: 'partial' })
      s.handleTestEvent({ key: 'batch:state:b', job_status: 'failed', queue_status: 'exited' })
      s.handleTestEvent({ key: 'batch:state:c', job_status: 'aborted', queue_status: 'exited' })

      expect(s.currentStrategyStatuses.value['state:a']).toBe('warning')
      expect(s.currentStrategyStatuses.value['state:b']).toBe('fail')
      expect(s.currentStrategyStatuses.value['state:c']).toBe('aborted')
    })

    it('a pending/requeued job status is surfaced verbatim ahead of queue_status', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:state:a', job_status: 'pending', queue_status: 'ready' })
      expect(s.currentStrategyStatuses.value['state:a']).toBe('pending')

      s.handleTestEvent({ key: 'batch:state:a', job_status: 'requeued', queue_status: 'ready' })
      expect(s.currentStrategyStatuses.value['state:a']).toBe('requeued')
    })
  })

  describe('rootStatus / rootBusy / rootButtonState / showCancelRoot', () => {
    it('idle by default, busy while pending/ready/running/paused, "running" button state only while actually running', () => {
      const s = mount()
      expect(s.rootStatus.value).toBe('idle')

      s.handleTestEvent({ key: 'batch:root', job_status: null, queue_status: 'ready' })
      expect(s.rootBusy.value).toBe(true)
      expect(s.rootButtonState.value).toBe('ready')

      s.handleTestEvent({ key: 'batch:root', job_status: null, queue_status: 'running' })
      expect(s.rootButtonState.value).toBe('running')
    })

    it('shows cancel only while hovering AND busy', () => {
      const s = mount()
      s.isHoveringRoot.value = true
      expect(s.showCancelRoot.value).toBe(false) // idle, not busy

      s.handleTestEvent({ key: 'batch:root', job_status: null, queue_status: 'running' })
      expect(s.showCancelRoot.value).toBe(true)

      s.isHoveringRoot.value = false
      expect(s.showCancelRoot.value).toBe(false)
    })
  })

  describe('handleTestEvent', () => {
    it('tracks the running token total off any message that carries one', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:root', job_status: 'running', queue_status: 'running', tokens: 1234 })
      expect(s.tokensBurnt.value).toBe(1234)
    })

    it('a completed session event reloads the run only if that session is currently selected under the same strategy', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'completed' }])
      const s = mount()
      await s.onSelect('session:1')
      getTests.mockClear()

      s.handleTestEvent({ key: 'batch:session:1', job_status: 'completed', queue_status: 'exited' })
      await vi.waitFor(() => expect(getTests).toHaveBeenCalled())
    })

    it('a completed session event for a different strategy does not reload', () => {
      const s = mount()
      s.selectedNodeId.value = 'session:1' // strategy stays 'batch'

      s.handleTestEvent({ key: 'turn_by_turn:session:1', job_status: 'completed', queue_status: 'exited' })

      expect(getTests).not.toHaveBeenCalled()
    })

    it('a completed non-session, non-root event fetches its aggregate result', async () => {
      getAggregateResult.mockResolvedValue({ name: 'state_accuracy', value: 0.9 })
      const s = mount()

      s.handleTestEvent({ key: 'batch:state:greeting', job_status: 'completed', queue_status: 'exited' })

      await vi.waitFor(() => expect(getAggregateResult).toHaveBeenCalledWith('proj', 'state', 'greeting', 'batch'))
      expect(s.nodeLastResult.value['batch:state:greeting']).toEqual({ name: 'state_accuracy', value: 0.9 })
    })

    it('root never fetches an aggregate result of its own', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:root', job_status: 'completed', queue_status: 'exited' })
      expect(getAggregateResult).not.toHaveBeenCalled()
    })

    it('a still-running event never fetches a result', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:state:greeting', job_status: 'running', queue_status: 'running' })
      expect(getAggregateResult).not.toHaveBeenCalled()
    })
  })

  describe('hydrateJobsStatus', () => {
    it('seeds completed sessions and aggregates, fetching results for the aggregates', async () => {
      getJobsStatus.mockResolvedValue({
        sessions: [{ session_id: 1, status: 'ok' }],
        aggregates: [{ kind: 'state', target: 'greeting', status: 'ok' }, { kind: 'signal', target: 'mood', status: 'failed' }],
      })
      getAggregateResult.mockResolvedValue({ name: 'state_accuracy', value: 1 })
      const s = mount()

      await vi.waitFor(() => expect(s.currentStrategyStatuses.value['session:1']).toBe('ok'))
      expect(s.currentStrategyStatuses.value['state:greeting']).toBe('ok')
      expect(s.currentStrategyStatuses.value['signal:mood']).toBeUndefined() // only 'ok' aggregates are seeded
      expect(getAggregateResult).toHaveBeenCalledWith('proj', 'state', 'greeting', 'batch')
    })
  })

  describe('onActivate dispatch by node kind', () => {
    it.each([
      ['session:1', () => postTest.mockResolvedValue({}), () => expect(postTest).toHaveBeenCalledWith('proj', 1, 'batch')],
      ['state:greeting', () => postStateTest.mockResolvedValue({}), () => expect(postStateTest).toHaveBeenCalledWith('proj', 'greeting', 'batch')],
      ['user:alice', () => postUserSessionsRun.mockResolvedValue({}), () => expect(postUserSessionsRun).toHaveBeenCalledWith('proj', 'alice', 'batch')],
      ['signal:mood', () => postSignalTest.mockResolvedValue({}), () => expect(postSignalTest).toHaveBeenCalledWith('proj', 'mood', 'batch')],
      ['sessions-branch', () => postSessionsRun.mockResolvedValue({}), () => expect(postSessionsRun).toHaveBeenCalledWith('proj', 'batch')],
      ['states-branch', () => postStatesAggregation.mockResolvedValue({}), () => expect(postStatesAggregation).toHaveBeenCalledWith('proj', 'batch')],
      ['users-branch', () => postUsersAggregation.mockResolvedValue({}), () => expect(postUsersAggregation).toHaveBeenCalledWith('proj', 'batch')],
      ['signals-branch', () => postSignalsAggregation.mockResolvedValue({}), () => expect(postSignalsAggregation).toHaveBeenCalledWith('proj', 'batch')],
      ['root', () => postRootAggregation.mockResolvedValue({}), () => expect(postRootAggregation).toHaveBeenCalledWith('proj', 'batch')],
    ])('%s dispatches to the right endpoint, selects the node, and marks it running first', async (nodeId, setup, assertion) => {
      setup()
      const s = mount()

      const activation = s.onActivate(nodeId)
      expect(s.selectedNodeId.value).toBe(nodeId)
      expect(s.currentStrategyStatuses.value[nodeId]).toBe('running')
      await activation

      assertion()
    })

    it('marks the node failed if the activation call rejects', async () => {
      postStateTest.mockRejectedValue(new Error('boom'))
      const s = mount()

      await s.onActivate('state:greeting')

      expect(s.currentStrategyStatuses.value['state:greeting']).toBe('fail')
    })

    it('every activation is pinned to the strategy active at launch time, even if it changes mid-flight', async () => {
      let resolvePost
      postStateTest.mockReturnValue(new Promise((resolve) => { resolvePost = resolve }))
      const s = mount()

      const activation = s.onActivate('state:greeting')
      strategy.value = 'turn_by_turn' // changes before the call resolves
      resolvePost({})
      await activation

      expect(postStateTest).toHaveBeenCalledWith('proj', 'greeting', 'batch')
      expect(s.currentStrategyStatuses.value['state:greeting']).toBeUndefined() // seeded under 'batch', we're reading 'turn_by_turn' now
    })
  })

  describe('onAbort / onActivateRoot', () => {
    it('onAbort deletes the job under the node\'s own strategy-scoped key', async () => {
      await mount().onAbort('state:greeting')
      expect(deleteTestJob).toHaveBeenCalledWith('proj', 'batch:state:greeting')
    })

    it('onActivateRoot cancels when hovering a busy root', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:root', job_status: null, queue_status: 'running' })
      s.isHoveringRoot.value = true

      s.onActivateRoot()

      expect(deleteTestJob).toHaveBeenCalledWith('proj', 'batch:root')
      expect(postRootAggregation).not.toHaveBeenCalled()
    })

    it('onActivateRoot does nothing while busy and not hovering', () => {
      const s = mount()
      s.handleTestEvent({ key: 'batch:root', job_status: null, queue_status: 'running' })

      s.onActivateRoot()

      expect(deleteTestJob).not.toHaveBeenCalled()
      expect(postRootAggregation).not.toHaveBeenCalled()
    })

    it('onActivateRoot activates when idle', () => {
      postRootAggregation.mockResolvedValue({})
      const s = mount()

      s.onActivateRoot()

      expect(postRootAggregation).toHaveBeenCalledWith('proj', 'batch')
    })
  })

  describe('onSelect / loadSelectedRun', () => {
    it('a non-session node just selects and emits, without fetching a run', async () => {
      const s = mount()
      await s.onSelect('state:greeting')

      expect(s.selectedNodeId.value).toBe('state:greeting')
      expect(emit).toHaveBeenCalledWith('select', 'state:greeting')
      expect(getTests).not.toHaveBeenCalled()
    })

    it('a session node fetches its runs, keeping only the active strategy\'s own', async () => {
      getTests.mockResolvedValue([
        { strategy: 'turn_by_turn', status: 'completed' },
        { strategy: 'batch', status: 'completed', error: null },
      ])
      const s = mount()

      await s.onSelect('session:1')

      expect(s.selectedRun.value).toEqual({ strategy: 'batch', status: 'completed', error: null })
      expect(s.currentStrategyStatuses.value['session:1']).toBe('ok')
    })

    it('a pending/running run is not written into nodeEvents (SSE owns that)', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'running' }])
      const s = mount()

      await s.onSelect('session:1')

      expect(s.currentStrategyStatuses.value['session:1']).toBeUndefined()
    })

    it('a failed run fetch clears the selected run instead of throwing', async () => {
      getTests.mockRejectedValue(new Error('boom'))
      const s = mount()

      await s.onSelect('session:1')

      expect(s.selectedRun.value).toBeNull()
      expect(s.selectedRunLoading.value).toBe(false)
    })

    it('switching strategy reloads the currently-selected session run', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'completed' }])
      const s = mount()
      await s.onSelect('session:1')
      getTests.mockClear()
      getTests.mockResolvedValue([{ strategy: 'turn_by_turn', status: 'completed' }])

      strategy.value = 'turn_by_turn'

      await vi.waitFor(() => expect(getTests).toHaveBeenCalled())
    })
  })

  describe('onResetCache', () => {
    it('batch strategy resets immediately, no confirmation needed', async () => {
      deleteTests.mockResolvedValue()
      const s = mount()
      s.handleTestEvent({ key: 'batch:root', job_status: 'completed', queue_status: 'exited' })

      await s.onResetCache()

      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteTests).toHaveBeenCalledWith('proj')
      expect(s.nodeEvents.value).toEqual({})
      expect(s.nodeLastResult.value).toEqual({})
    })

    it('turn_by_turn strategy asks for confirmation first; declining skips the reset', async () => {
      strategy.value = 'turn_by_turn'
      confirmDialog.mockResolvedValue(false)
      const s = mount()

      await s.onResetCache()

      expect(deleteTests).not.toHaveBeenCalled()
    })

    it('reloads the selected run afterwards if a session is selected', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'completed' }])
      deleteTests.mockResolvedValue()
      const s = mount()
      await s.onSelect('session:1')
      getTests.mockClear()

      await s.onResetCache()

      expect(getTests).toHaveBeenCalled()
    })
  })

  describe('selectedNodeLabel / signalLabel', () => {
    it('resolves every node kind to a human label', async () => {
      const s = mount()
      s.selectedNodeId.value = 'root'
      expect(s.selectedNodeLabel.value).toBe('proj')

      s.selectedNodeId.value = 'session:1'
      expect(s.selectedNodeLabel.value).toBe('My session')

      s.selectedNodeId.value = 'session:999'
      expect(s.selectedNodeLabel.value).toBe('Session 999')

      s.selectedNodeId.value = 'state:greeting'
      expect(s.selectedNodeLabel.value).toBe('greeting')

      s.selectedNodeId.value = 'signal:mood'
      expect(s.selectedNodeLabel.value).toBe('Mood')

      s.selectedNodeId.value = 'signal:unknown'
      expect(s.selectedNodeLabel.value).toBe('unknown')
    })
  })

  it('anyTestExecuted reflects whether any node has ever produced an event', () => {
    const s = mount()
    expect(s.anyTestExecuted.value).toBe(false)
    s.handleTestEvent({ key: 'batch:state:a', job_status: 'running', queue_status: 'running' })
    expect(s.anyTestExecuted.value).toBe(true)
  })
})
