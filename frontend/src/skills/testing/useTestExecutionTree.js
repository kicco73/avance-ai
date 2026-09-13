import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  deleteAllTestJobs, deleteTestJob, deleteTests, getAggregateResult, getTests, getTestStatus,
  postTest, postRootAggregation, postSessionsRun, postSignalTest, postSignalsAggregation,
  postStateTest, postStatesAggregation, postUserSessionsRun, postUsersAggregation,
} from './api.js'
import { busChannel } from '../../busChannel.js'
import { confirmDialog } from '../../dialogStore.js'

export function useTestExecutionTree(projectId, strategy, sessions, projectSignals, emit) {
  let unsubscribeTestUpdates = null

  const tokensTotal = ref(null)
  const tokensBaselineByStrategy = ref({})
  const tokensBurnt = computed(() => {
    const baseline = tokensBaselineByStrategy.value[strategy.value]
    if (tokensTotal.value == null || baseline == null) return 0
    return Math.max(0, tokensTotal.value - baseline)
  })

  function cacheKey(strategyName, nodeId) {
    return `${strategyName}:${nodeId}`
  }

  const nodeEvents = ref({})
  const nodeLastResult = ref({})

  const selectedNodeId = ref(null)
  const selectedRun = ref(null)
  const selectedRunLoading = ref(false)

  function statusFromOutcome(status, error) {
    if (status === 'failed') return 'fail'
    if (status === 'aborted') return 'aborted'
    if (status === 'completed') return error ? 'warning' : 'ok'
    return 'running'
  }

  function outcome(message) {
    if (!message) return 'idle'
    if (message.queue_status === 'exited') return statusFromOutcome(message.job_status, message.error)
    if (message.job_status === 'pending') return 'pending'
    if (message.job_status === 'requeued') return 'requeued'
    return message.queue_status
  }

  const currentStrategyStatuses = computed(() => {
    const prefix = `${strategy.value}:`
    const result = {}
    for (const [key, message] of Object.entries(nodeEvents.value)) {
      if (key.startsWith(prefix)) result[key.slice(prefix.length)] = outcome(message)
    }
    return result
  })

  const currentStrategyProgress = computed(() => {
    const prefix = `${strategy.value}:`
    const result = {}
    for (const [key, message] of Object.entries(nodeEvents.value)) {
      if (key.startsWith(prefix) && message.percentage != null) {
        result[key.slice(prefix.length)] = message.percentage
      }
    }
    return result
  })

  const selectedCacheKey = computed(() => (
    selectedNodeId.value ? cacheKey(strategy.value, selectedNodeId.value) : null
  ))

  const selectedNodeError = computed(() => {
    const message = nodeEvents.value[selectedCacheKey.value]
    return message?.job_status === 'failed' ? message.error : null
  })

  function setNodeEvent(key, jobStatus, error = null) {
    const queueStatus = (
      jobStatus === 'completed' || jobStatus === 'failed' || jobStatus === 'aborted' ? 'exited'
      : jobStatus === 'running' ? 'running' : 'ready'
    )
    nodeEvents.value = { ...nodeEvents.value, [key]: { key, job_status: jobStatus, queue_status: queueStatus, percentage: null, error } }
  }

  function aggregateKindAndTarget(nodeId) {
    if (nodeId.startsWith('state:')) return { kind: 'state', target: nodeId.slice('state:'.length) }
    if (nodeId.startsWith('signal:')) return { kind: 'signal', target: nodeId.slice('signal:'.length) }
    if (nodeId.startsWith('user:')) return { kind: 'user_sessions', target: nodeId.slice('user:'.length) }
    if (nodeId === 'sessions-branch') return { kind: 'sessions', target: null }
    if (nodeId === 'users-branch') return { kind: 'users', target: null }
    if (nodeId === 'states-branch') return { kind: 'all_states', target: null }
    if (nodeId === 'signals-branch') return { kind: 'all_signals', target: null }
    return null
  }

  async function fetchAggregateResult(key, eventStrategy, kind, target) {
    try {
      const result = await getAggregateResult(projectId, kind, target, eventStrategy)
      nodeLastResult.value = { ...nodeLastResult.value, [key]: result }
    } catch {
    }
  }

  function handleTestEvent(message) {
    nodeEvents.value = { ...nodeEvents.value, [message.key]: message }

    const { key, job_status: status, queue_status: queueStatus } = message
    const separatorIndex = key.indexOf(':')
    const eventStrategy = key.slice(0, separatorIndex)
    const nodeId = key.slice(separatorIndex + 1)

    if (typeof message.tokens === 'number') tokensTotal.value = message.tokens
    if (nodeId.startsWith('session:')) {
      if (selectedNodeId.value === nodeId && strategy.value === eventStrategy) loadSelectedRun(nodeId)
      return
    }
    if (queueStatus !== 'exited' || status !== 'completed') return
    const target = aggregateKindAndTarget(nodeId)
    if (target == null) return
    fetchAggregateResult(key, eventStrategy, target.kind, target.target)
  }

  async function activateSessionLeaf(nodeId, activeStrategy) {
    const key = cacheKey(activeStrategy, nodeId)
    setNodeEvent(key, 'running')
    try {
      const sessionId = Number(nodeId.slice('session:'.length))
      await postTest(projectId, sessionId, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateStateLeaf(nodeId, activeStrategy) {
    const key = cacheKey(activeStrategy, nodeId)
    setNodeEvent(key, 'running')
    try {
      const stateKey = nodeId.slice('state:'.length)
      await postStateTest(projectId, stateKey, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateSessionsRun(activeStrategy) {
    const key = cacheKey(activeStrategy, 'sessions-branch')
    setNodeEvent(key, 'running')
    try {
      await postSessionsRun(projectId, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateAllStates(activeStrategy) {
    const key = cacheKey(activeStrategy, 'states-branch')
    setNodeEvent(key, 'running')
    try {
      await postStatesAggregation(projectId, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateSignalLeaf(nodeId, activeStrategy) {
    const key = cacheKey(activeStrategy, nodeId)
    setNodeEvent(key, 'running')
    try {
      const signalName = nodeId.slice('signal:'.length)
      await postSignalTest(projectId, signalName, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateAllSignals(activeStrategy) {
    const key = cacheKey(activeStrategy, 'signals-branch')
    setNodeEvent(key, 'running')
    try {
      await postSignalsAggregation(projectId, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  function signalLabel(name) {
    return projectSignals.value.find((signal) => signal.name === name)?.ui_label || name
  }

  async function activateUserLeaf(nodeId, activeStrategy) {
    const key = cacheKey(activeStrategy, nodeId)
    setNodeEvent(key, 'running')
    try {
      const username = nodeId.slice('user:'.length)
      await postUserSessionsRun(projectId, username, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateUsersAggregation(activeStrategy) {
    const key = cacheKey(activeStrategy, 'users-branch')
    setNodeEvent(key, 'running')
    try {
      await postUsersAggregation(projectId, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function activateRoot(activeStrategy) {
    const key = cacheKey(activeStrategy, 'root')
    setNodeEvent(key, 'running')
    try {
      await postRootAggregation(projectId, activeStrategy)
    } catch {
      setNodeEvent(key, 'failed')
    }
  }

  async function onActivate(nodeId) {
    onSelect(nodeId)
    const activeStrategy = strategy.value
    if (!currentStrategyBusy.value) {
      tokensBaselineByStrategy.value = {
        ...tokensBaselineByStrategy.value, [activeStrategy]: tokensTotal.value ?? 0
      }
    }
    if (nodeId.startsWith('session:')) {
      await activateSessionLeaf(nodeId, activeStrategy)
    } else if (nodeId.startsWith('state:')) {
      await activateStateLeaf(nodeId, activeStrategy)
    } else if (nodeId.startsWith('user:')) {
      await activateUserLeaf(nodeId, activeStrategy)
    } else if (nodeId.startsWith('signal:')) {
      await activateSignalLeaf(nodeId, activeStrategy)
    } else if (nodeId === 'sessions-branch') {
      await activateSessionsRun(activeStrategy)
    } else if (nodeId === 'states-branch') {
      await activateAllStates(activeStrategy)
    } else if (nodeId === 'users-branch') {
      await activateUsersAggregation(activeStrategy)
    } else if (nodeId === 'signals-branch') {
      await activateAllSignals(activeStrategy)
    } else if (nodeId === 'root') {
      await activateRoot(activeStrategy)
    }
  }

  async function onAbort(nodeId) {
    try {
      await deleteTestJob(projectId, cacheKey(strategy.value, nodeId))
    } catch {
    }
  }

  async function onActivateRoot() {
    if (currentStrategyBusy.value) {
      try {
        await deleteAllTestJobs(projectId)
      } catch {
      }
      return
    }
    onActivate('root')
  }

  async function loadSelectedRun(nodeId) {
    const sessionId = Number(nodeId.slice('session:'.length))
    selectedRunLoading.value = true
    try {
      const runs = await getTests(projectId, sessionId)
      const run = runs.find((run) => run.strategy === strategy.value) ?? null
      selectedRun.value = run
      if (run != null && run.status !== 'pending' && run.status !== 'running') {
        setNodeEvent(cacheKey(strategy.value, nodeId), run.status, run.error)
      }
    } catch {
      selectedRun.value = null
    } finally {
      selectedRunLoading.value = false
    }
  }

  function isRunNode(nodeId) {
    return nodeId.startsWith('session:')
  }

  async function onSelect(nodeId) {
    selectedNodeId.value = nodeId
    emit('select', nodeId)
    selectedRun.value = null
    if (!isRunNode(nodeId)) return
    await loadSelectedRun(nodeId)
  }

  watch(strategy, () => {
    if (selectedNodeId.value && isRunNode(selectedNodeId.value)) {
      loadSelectedRun(selectedNodeId.value)
    }
  })

  const selectedNodeLabel = computed(() => {
    const nodeId = selectedNodeId.value
    if (!nodeId) return ''
    if (nodeId === 'root') return projectId
    if (nodeId === 'sessions-branch') return 'Sessions'
    if (nodeId === 'states-branch') return 'Stats'
    if (nodeId === 'users-branch') return 'Users'
    if (nodeId === 'signals-branch') return 'Signals'
    if (nodeId.startsWith('session:')) {
      const id = Number(nodeId.slice('session:'.length))
      const session = sessions.value.find((s) => s.id === id)
      return session ? (session.title || session.end_state || `Session ${id}`) : `Session ${id}`
    }
    if (nodeId.startsWith('state:')) return nodeId.slice('state:'.length)
    if (nodeId.startsWith('user:')) return nodeId.slice('user:'.length)
    if (nodeId.startsWith('signal:')) return signalLabel(nodeId.slice('signal:'.length))
    return nodeId
  })

  const resettingCache = ref(false)

  const anyTestExecuted = computed(() => (
    Object.keys(nodeEvents.value).some((key) => key.startsWith(`${strategy.value}:`))
  ))

  const anyJobBusy = computed(() => (
    Object.values(nodeEvents.value).some((message) => (
      ['pending', 'ready', 'running', 'paused', 'requeued'].includes(outcome(message))
    ))
  ))

  const currentStrategyBusy = computed(() => (
    Object.entries(nodeEvents.value).some(([key, message]) => (
      key.startsWith(`${strategy.value}:`) && ['pending', 'ready', 'running', 'paused', 'requeued'].includes(outcome(message))
    ))
  ))

  async function onResetCache() {
    if (strategy.value === 'turn_by_turn') {
      const ok = await confirmDialog({
        title: 'Reset test cache',
        body: 'Turn-by-turn tests replay one AI call per message — resetting the cache forces every test to run again from scratch, which can be expensive. Continue?',
        okLabel: 'Reset',
        danger: true
      })
      if (!ok) return
    }
    resettingCache.value = true
    try {
      await deleteTests(projectId)
      nodeEvents.value = {}
      nodeLastResult.value = {}
      tokensBaselineByStrategy.value = {}
      selectedRun.value = null
      if (selectedNodeId.value && isRunNode(selectedNodeId.value)) {
        await loadSelectedRun(selectedNodeId.value)
      }
    } catch {
    } finally {
      resettingCache.value = false
    }
  }

  onMounted(async () => {
    onSelect('root')
    unsubscribeTestUpdates = busChannel.subscribe('ui.progress', handleTestEvent)
    const { events, tokens } = await getTestStatus(projectId)
    if (typeof tokens === 'number') tokensTotal.value = tokens
    events.forEach((message) => {
      if (!(message.key in nodeEvents.value)) handleTestEvent(message)
    })
  })

  onBeforeUnmount(() => {
    unsubscribeTestUpdates?.()
    unsubscribeTestUpdates = null
  })

  return {
    tokensBurnt, nodeEvents, nodeLastResult, selectedNodeId, selectedRun, selectedRunLoading,
    currentStrategyStatuses, currentStrategyProgress,
    selectedCacheKey, selectedNodeError, selectedNodeLabel, signalLabel, anyTestExecuted, anyJobBusy, currentStrategyBusy,
    handleTestEvent,
    onActivate, onAbort, onActivateRoot, onSelect,
    resettingCache, onResetCache,
  }
}
