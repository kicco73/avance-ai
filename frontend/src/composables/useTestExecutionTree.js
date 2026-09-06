import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  deleteAllTestJobs, deleteTestJob, deleteTests, getAggregateResult, getTests, getTestStatus,
  postTest, postRootAggregation, postSessionsRun, postSignalTest, postSignalsAggregation,
  postStateTest, postStatesAggregation, postUserSessionsRun, postUsersAggregation,
} from '../api.js'
import { onTestUpdate } from '../chatClient.js'
import { confirmDialog } from '../dialogStore.js'

// ProjectTestPanel.vue's own execution tree: per-node job status/progress
// (live over the shared /ws/notifications connection, seeded once from a
// REST snapshot), which node is selected, and dispatching a "run" click
// to the right endpoint for that node's own kind. `strategy` is the
// shared batch/turn_by_turn control (owned by the caller, read here);
// `sessions`/`projectSignals` are reference data the caller already
// loads, consulted only for display labels; `emit` is the component's
// own defineEmits('select').
export function useTestExecutionTree(projectId, strategy, sessions, projectSignals, emit) {
  // Running total of AI tokens consumed so far — piggybacked onto every
  // test-update message by the backend's QueueProgressBroadcaster (see
  // AiService.get_total_tokens), not fetched separately.
  const tokensBurntByStrategy = ref({})
  const tokensBurnt = computed(() => tokensBurntByStrategy.value[strategy.value] ?? 0)

  // Every cache below is keyed by `${strategy}:${nodeId}`, never nodeId
  // alone — turn_by_turn and batch results aren't comparable, so switching
  // strategy must never show the other strategy's cached status/result for
  // the same node.
  function cacheKey(strategyName, nodeId) {
    return `${strategyName}:${nodeId}`
  }

  // One raw snapshot per event key (`${strategy}:${nodeId}`) — the last
  // status message received for that node, kept whole. Displayed status,
  // error, and progress are all derived from it on read (see outcome()
  // below), never split into separate stores that could drift apart from
  // one another as new events arrive.
  const nodeEvents = ref({})
  // A node's own most recent aggregate result payload — fetched over REST
  // once its job completes, a genuinely different piece of data (and a
  // different source) from the status stream above, so it stays separate.
  const nodeLastResult = ref({})

  const selectedNodeId = ref(null)
  const selectedRun = ref(null)
  const selectedRunLoading = ref(false)

  // completed with no error -> ok; completed but error carries text (one
  // or more sessions skipped, e.g. no known starting state) -> warning,
  // never a threshold on the metrics themselves. failed -> fail.
  function statusFromOutcome(status, error) {
    if (status === 'failed') return 'fail'
    if (status === 'aborted') return 'aborted'
    if (status === 'completed') return error ? 'warning' : 'ok'
    return 'running'
  }

  // A node with no event yet falls back to TestsTree's own implicit 'idle'.
  // ready/running/paused/exited are the QUEUE's own view of this job, not
  // the job's (see JobQueue._broadcast_status/ThrottledJobQueue._throttle)
  // — is a worker actively inside its step right now, asleep waiting out
  // the rate limit, or neither? That's exactly the ready-vs-running-vs-
  // paused split the UI shows. job_status (job.status() itself: pending/
  // running/completed/failed) only matters once queue_status says
  // 'exited' (to read the real outcome), or while it's still 'pending' —
  // the one instant before Job.prepare() runs, when nothing (not even a
  // step count) is known yet, which needs its own distinct spin instead of
  // reading as an ordinary queued 'ready' (which usually already has a
  // real, worth-persisting percentage behind it).
  function outcome(message) {
    if (!message) return 'idle'
    if (message.queue_status === 'exited') return statusFromOutcome(message.job_status, message.error)
    if (message.job_status === 'pending') return 'pending'
    if (message.job_status === 'requeued') return 'requeued'
    return message.queue_status
  }

  // TestsTree only ever sees the active strategy's own statuses/progress —
  // a node from the other strategy must never leak through.
  const currentStrategyStatuses = computed(() => {
    const prefix = `${strategy.value}:`
    const result = {}
    for (const [key, message] of Object.entries(nodeEvents.value)) {
      if (key.startsWith(prefix)) result[key.slice(prefix.length)] = outcome(message)
    }
    return result
  })

  // message.percentage tracks the job's own overall progress (steps_done /
  // total_steps) — true regardless of queue_status, so it must stay
  // visible through every 'ready' gap between steps too. Gating this on
  // queue_status === 'running' made the percentage vanish and the ring
  // snap back to an indeterminate spin the instant a job was re-queued for
  // its next step, even though nothing about its actual progress changed.
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

  // Writes one node's event as a single, complete replacement — used both
  // for real test-update messages (job_status/queue_status straight from
  // the backend, see JobQueue._broadcast_status) and for the optimistic
  // 'running'/'completed'/'failed' the activate*() functions below set on
  // click, before the first real one arrives — jobStatus here is simple
  // on purpose, so it's translated into the same two-field shape a real
  // message carries, and outcome() never needs to special-case its origin.
  function setNodeEvent(key, jobStatus, error = null) {
    const queueStatus = (
      jobStatus === 'completed' || jobStatus === 'failed' || jobStatus === 'aborted' ? 'exited'
      : jobStatus === 'running' ? 'running' : 'ready'
    )
    nodeEvents.value = { ...nodeEvents.value, [key]: { key, job_status: jobStatus, queue_status: queueStatus, percentage: null, error } }
  }

  // nodeId's own {kind, target} in the aggregate-result vocabulary — null
  // for 'session:*' and 'root', neither of which is one.
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
      // already surfaced via apiFetch
    }
  }

  // The single live-update channel for every node's status/progress/result
  // — connected once in onMounted, replacing all per-node polling. Each
  // message replaces its node's whole event record in one write (see
  // nodeEvents/setNodeEvent above), so a fresh 'pending'/'running' for a
  // re-run can never leave a stale error behind from the previous attempt.
  function handleTestEvent(message) {
    nodeEvents.value = { ...nodeEvents.value, [message.key]: message }

    const { key, job_status: status, queue_status: queueStatus } = message
    const separatorIndex = key.indexOf(':')
    const eventStrategy = key.slice(0, separatorIndex)
    const nodeId = key.slice(separatorIndex + 1)

    if (typeof message.tokens === 'number') {
      tokensBurntByStrategy.value = { ...tokensBurntByStrategy.value, [eventStrategy]: message.tokens }
    }
    if (nodeId.startsWith('session:')) {
      if (selectedNodeId.value === nodeId && strategy.value === eventStrategy) loadSelectedRun(nodeId)
      return
    }
    if (queueStatus !== 'exited' || status !== 'completed') return
    const target = aggregateKindAndTarget(nodeId)
    if (target == null) return // root — no result of its own
    fetchAggregateResult(key, eventStrategy, target.kind, target.target)
  }

  async function activateSessionLeaf(nodeId, activeStrategy) {
    const key = cacheKey(activeStrategy, nodeId)
    setNodeEvent(key, 'running')
    try {
      const sessionId = Number(nodeId.slice('session:'.length))
      await postTest(projectId, sessionId, activeStrategy)
    } catch {
      // already surfaced via apiFetch
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
      // already surfaced via apiFetch
      setNodeEvent(key, 'failed')
    }
  }

  async function activateSessionsRun(activeStrategy) {
    const key = cacheKey(activeStrategy, 'sessions-branch')
    setNodeEvent(key, 'running')
    try {
      await postSessionsRun(projectId, activeStrategy)
    } catch {
      // already surfaced via apiFetch
      setNodeEvent(key, 'failed')
    }
  }

  async function activateAllStates(activeStrategy) {
    const key = cacheKey(activeStrategy, 'states-branch')
    setNodeEvent(key, 'running')
    try {
      await postStatesAggregation(projectId, activeStrategy)
    } catch {
      // already surfaced via apiFetch
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
      // already surfaced via apiFetch
      setNodeEvent(key, 'failed')
    }
  }

  async function activateAllSignals(activeStrategy) {
    const key = cacheKey(activeStrategy, 'signals-branch')
    setNodeEvent(key, 'running')
    try {
      await postSignalsAggregation(projectId, activeStrategy)
    } catch {
      // already surfaced via apiFetch
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
      // already surfaced via apiFetch
      setNodeEvent(key, 'failed')
    }
  }

  async function activateUsersAggregation(activeStrategy) {
    const key = cacheKey(activeStrategy, 'users-branch')
    setNodeEvent(key, 'running')
    try {
      await postUsersAggregation(projectId, activeStrategy)
    } catch {
      // already surfaced via apiFetch
      setNodeEvent(key, 'failed')
    }
  }

  async function activateRoot(activeStrategy) {
    const key = cacheKey(activeStrategy, 'root')
    setNodeEvent(key, 'running')
    try {
      await postRootAggregation(projectId, activeStrategy)
    } catch {
      // already surfaced via apiFetch
      setNodeEvent(key, 'failed')
    }
  }

  async function onActivate(nodeId) {
    // Pressing play selects the node it belongs to, same as clicking its
    // row — the results panel should already be pointed at it once the
    // run/job(s) finish.
    onSelect(nodeId)
    // Snapshot the strategy at launch time — every job this dispatches is
    // pinned to it regardless of whether the dropdown changes before they finish.
    const activeStrategy = strategy.value
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

  // The running job's own key already matches cacheKey(strategy, nodeId)
  // verbatim (see JobQueue._broadcast_status's "key") -- no per-node-kind
  // dispatch needed here, unlike onActivate above.
  async function onAbort(nodeId) {
    try {
      await deleteTestJob(projectId, cacheKey(strategy.value, nodeId))
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function onActivateRoot() {
    if (currentStrategyBusy.value) {
      try {
        await deleteAllTestJobs(projectId)
      } catch {
        // already surfaced via apiFetch
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
      // Already most-recent-first (see backend TestService.list_runs)
      // — filtered to the active strategy, since turn_by_turn and batch
      // runs aren't comparable and must never be shown as if they were.
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

  // Switching strategy must refresh whatever's on screen for the currently
  // selected node — otherwise it would keep showing the other strategy's
  // last-fetched run.
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

  // reset_cache() wipes every test row project-wide, every strategy at
  // once — a job still in flight under any of them must be stopped or let
  // finish first, so this checks every tracked node's raw status, not
  // just the currently selected strategy's own view of them.
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
      tokensBurntByStrategy.value = {}
      selectedRun.value = null
      if (selectedNodeId.value && isRunNode(selectedNodeId.value)) {
        await loadSelectedRun(selectedNodeId.value)
      }
    } catch {
      // already surfaced via apiFetch
    } finally {
      resettingCache.value = false
    }
  }

  onMounted(async () => {
    // selectedNodeId always starts null on a fresh mount (this tab isn't
    // kept alive while closed — see EditProjectView.vue's autoOpen v-if),
    // so there's never anything already selected to defer to here.
    onSelect('root')
    // Live updates arrive over the shared /ws/notifications connection
    // (see chatClient.js's onTestUpdate) regardless of which page is open;
    // the snapshot fetched here just catches this node up on whatever
    // happened before this component existed — handleTestEvent needs no
    // special-casing for it, it's shaped exactly like a live update.
    // Registered before the await, so a live update landing mid-fetch is
    // never clobbered by the (now stale) snapshot value for that same key.
    onTestUpdate(handleTestEvent)
    const { events } = await getTestStatus(projectId)
    events.forEach((message) => {
      if (!(message.key in nodeEvents.value)) handleTestEvent(message)
    })
  })

  onBeforeUnmount(() => {
    onTestUpdate(null)
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
