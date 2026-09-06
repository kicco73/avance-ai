import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  deleteAllTestJobs: vi.fn(),
  deleteTestJob: vi.fn(),
  deleteTests: vi.fn(),
  getAggregateResult: vi.fn(),
  getTests: vi.fn(),
  getTestStatus: vi.fn(),
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
vi.mock('../src/chatClient.js', () => ({
  onTestUpdate: vi.fn(),
}))

import {
  deleteAllTestJobs, deleteTestJob, deleteTests, getAggregateResult, getTests, getTestStatus,
  postTest, postRootAggregation, postSessionsRun, postSignalTest, postSignalsAggregation,
  postStateTest, postStatesAggregation, postUserSessionsRun, postUsersAggregation,
} from '../src/api.js'
import { confirmDialog } from '../src/dialogStore.js'
import { onTestUpdate } from '../src/chatClient.js'
import { useTestExecutionTree } from '../src/composables/useTestExecutionTree.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

describe('useTestExecutionTree', () => {
  let unmount, emit, strategy, sessions, projectSignals

  beforeEach(() => {
    vi.clearAllMocks()
    getTestStatus.mockResolvedValue({ events: [] })
    emit = vi.fn()
    strategy = ref('batch')
    sessions = ref([{ id: 1, title: 'My session' }])
    projectSignals = ref([{ name: 'mood', ui_label: 'Mood' }])
  })

  afterEach(() => {
    unmount?.()
    unmount = null
  })

  function mount() {
    const mounted = mountComposable(() => useTestExecutionTree('proj', strategy, sessions, projectSignals, emit))
    unmount = mounted.unmount
    return mounted.result
  }

  describe('onMounted wiring', () => {
    it('selects root, subscribes to live updates, seeds nodeEvents from the snapshot, unsubscribes on unmount', async () => {
      getTestStatus.mockResolvedValue({
        events: [{ key: 'batch:state:greeting', job_status: 'running', queue_status: 'running' }],
      })
      const s = mount()

      expect(s.selectedNodeId.value).toBe('root')
      expect(emit).toHaveBeenCalledWith('select', 'root')
      expect(getTestStatus).toHaveBeenCalledWith('proj')
      expect(onTestUpdate).toHaveBeenCalledWith(s.handleTestEvent)
      await vi.waitFor(() => expect(s.currentStrategyStatuses.value['state:greeting']).toBe('running'))

      unmount()
      unmount = null
      expect(onTestUpdate).toHaveBeenLastCalledWith(null)
    })

    it('a live update landing while the snapshot is still in flight is not overwritten by the (now stale) snapshot value for that key', async () => {
      let resolveSnapshot
      getTestStatus.mockReturnValue(new Promise((resolve) => { resolveSnapshot = resolve }))
      const s = mount()
      const liveHandler = onTestUpdate.mock.calls[0][0]

      liveHandler({ key: 'batch:state:greeting', job_status: 'completed', queue_status: 'exited' })
      resolveSnapshot({
        events: [{ key: 'batch:state:greeting', job_status: 'running', queue_status: 'running' }],
      })
      await vi.waitFor(() => expect(getTestStatus).toHaveBeenCalled())

      expect(s.currentStrategyStatuses.value['state:greeting']).toBe('ok')
    })
  })

  describe('currentStrategyStatuses / currentStrategyProgress / anyTestExecuted', () => {
    it('surfaces only the active strategy, keyed by bare nodeId, mapping every job/queue status combination', () => {
      const s = mount()
      expect(s.anyTestExecuted.value).toBe(false)

      s.handleTestEvent({ key: 'batch:state:a', job_status: 'running', queue_status: 'running', percentage: 40 })
      s.handleTestEvent({ key: 'turn_by_turn:state:a', job_status: 'completed', queue_status: 'exited' })
      s.handleTestEvent({ key: 'batch:state:b', job_status: 'completed', queue_status: 'exited', error: 'partial' })
      s.handleTestEvent({ key: 'batch:state:c', job_status: 'failed', queue_status: 'exited' })
      s.handleTestEvent({ key: 'batch:state:d', job_status: 'aborted', queue_status: 'exited' })
      s.handleTestEvent({ key: 'batch:state:e', job_status: 'pending', queue_status: 'ready' })
      expect(s.currentStrategyStatuses.value['state:e']).toBe('pending')
      s.handleTestEvent({ key: 'batch:state:e', job_status: 'requeued', queue_status: 'ready' })

      expect(s.currentStrategyStatuses.value).toEqual({
        'state:a': 'running',
        'state:b': 'warning',
        'state:c': 'fail',
        'state:d': 'aborted',
        'state:e': 'requeued',
      })
      expect(s.currentStrategyProgress.value).toEqual({ 'state:a': 40 })
      expect(s.anyTestExecuted.value).toBe(true)
    })
  })

  describe('handleTestEvent', () => {
    it('tracks tokens off any message, and fetches an aggregate result only for completed non-root, non-session nodes', async () => {
      getAggregateResult.mockResolvedValue({ name: 'state_accuracy', value: 0.9 })
      const s = mount()

      s.handleTestEvent({ key: 'batch:root', job_status: 'running', queue_status: 'running', tokens: 1234 })
      expect(s.tokensBurnt.value).toBe(1234)
      s.handleTestEvent({ key: 'batch:root', job_status: 'completed', queue_status: 'exited' })
      s.handleTestEvent({ key: 'batch:state:greeting', job_status: 'running', queue_status: 'running' })
      expect(getAggregateResult).not.toHaveBeenCalled()

      s.handleTestEvent({ key: 'batch:state:greeting', job_status: 'completed', queue_status: 'exited' })

      await vi.waitFor(() => expect(getAggregateResult).toHaveBeenCalledWith('proj', 'state', 'greeting', 'batch'))
      expect(s.nodeLastResult.value['batch:state:greeting']).toEqual({ name: 'state_accuracy', value: 0.9 })
    })

    it('a completed session event reloads the run only if that session is selected under the same strategy', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'completed' }])
      const s = mount()
      await s.onSelect('session:1')
      getTests.mockClear()

      s.handleTestEvent({ key: 'turn_by_turn:session:1', job_status: 'completed', queue_status: 'exited' })
      expect(getTests).not.toHaveBeenCalled()

      s.handleTestEvent({ key: 'batch:session:1', job_status: 'completed', queue_status: 'exited' })
      await vi.waitFor(() => expect(getTests).toHaveBeenCalled())
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

    it('pins each activation to the strategy active at launch time, and marks the node failed if the call rejects', async () => {
      let resolvePost
      postStateTest.mockReturnValue(new Promise((resolve) => { resolvePost = resolve }))
      const s = mount()

      const activation = s.onActivate('state:greeting')
      strategy.value = 'turn_by_turn'
      resolvePost({})
      await activation

      expect(postStateTest).toHaveBeenCalledWith('proj', 'greeting', 'batch')
      expect(s.currentStrategyStatuses.value['state:greeting']).toBeUndefined()

      postStateTest.mockRejectedValue(new Error('boom'))
      await s.onActivate('state:greeting')

      expect(postStateTest).toHaveBeenLastCalledWith('proj', 'greeting', 'turn_by_turn')
      expect(s.currentStrategyStatuses.value['state:greeting']).toBe('fail')
    })
  })

  describe('onAbort / onActivateRoot', () => {
    it('onActivateRoot activates when idle and cancels every job when busy; onAbort deletes the strategy-scoped job', async () => {
      postRootAggregation.mockResolvedValue({})
      const s = mount()

      s.onActivateRoot()
      expect(postRootAggregation).toHaveBeenCalledWith('proj', 'batch')

      s.handleTestEvent({ key: 'batch:root', job_status: null, queue_status: 'running' })
      s.onActivateRoot()
      expect(deleteAllTestJobs).toHaveBeenCalledWith('proj')
      expect(postRootAggregation).toHaveBeenCalledTimes(1)

      await s.onAbort('state:greeting')
      expect(deleteTestJob).toHaveBeenCalledWith('proj', 'batch:state:greeting')
    })
  })

  describe('onSelect / loadSelectedRun', () => {
    it('only session nodes fetch runs, keeping the active strategy\'s own, and a strategy switch reloads them', async () => {
      getTests.mockResolvedValue([
        { strategy: 'turn_by_turn', status: 'completed' },
        { strategy: 'batch', status: 'completed', error: null },
      ])
      const s = mount()

      await s.onSelect('state:greeting')
      expect(s.selectedNodeId.value).toBe('state:greeting')
      expect(emit).toHaveBeenCalledWith('select', 'state:greeting')
      expect(getTests).not.toHaveBeenCalled()

      await s.onSelect('session:1')
      expect(s.selectedRun.value).toEqual({ strategy: 'batch', status: 'completed', error: null })
      expect(s.currentStrategyStatuses.value['session:1']).toBe('ok')

      getTests.mockClear()
      strategy.value = 'turn_by_turn'
      await vi.waitFor(() => expect(getTests).toHaveBeenCalled())
    })

    it('a pending/running run is not written into nodeEvents, and a failed fetch clears the selected run', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'running' }])
      const s = mount()

      await s.onSelect('session:1')
      expect(s.currentStrategyStatuses.value['session:1']).toBeUndefined()

      getTests.mockRejectedValue(new Error('boom'))
      await s.onSelect('session:1')

      expect(s.selectedRun.value).toBeNull()
      expect(s.selectedRunLoading.value).toBe(false)
    })
  })

  describe('onResetCache', () => {
    it('batch strategy resets immediately and reloads the selected session run', async () => {
      getTests.mockResolvedValue([{ strategy: 'batch', status: 'completed' }])
      deleteTests.mockResolvedValue()
      const s = mount()
      await s.onSelect('session:1')
      s.handleTestEvent({ key: 'batch:root', job_status: 'completed', queue_status: 'exited' })
      getTests.mockClear()

      await s.onResetCache()

      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteTests).toHaveBeenCalledWith('proj')
      expect(getTests).toHaveBeenCalled()
      expect(Object.keys(s.nodeEvents.value)).toEqual(['batch:session:1'])
      expect(s.nodeLastResult.value).toEqual({})
    })

    it('turn_by_turn strategy asks for confirmation first; declining skips the reset', async () => {
      strategy.value = 'turn_by_turn'
      confirmDialog.mockResolvedValue(false)
      const s = mount()

      await s.onResetCache()

      expect(deleteTests).not.toHaveBeenCalled()
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
})
