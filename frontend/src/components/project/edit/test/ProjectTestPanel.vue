<script setup>
// "Test" mode's content, shown when EditProjectView.vue's `testOpen` is set.
// Two columns: TestsTree on the left (Sessions/States), a node's results on
// the right. Owns all data fetching/launching/polling — TestsTree itself
// (alongside TestNodeButton, both in this same test/ folder) stays purely presentational.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TestsTree from './TestsTree.vue'
import SignalAccuracyDistributionChart from './SignalAccuracyDistributionChart.vue'
import DocInfoButton from '../../../DocInfoButton.vue'
import MetricDetail from '../../../inspector/MetricDetail.vue'
import {
  createTestEventsSource, deleteTests, getAggregateResult, getTestMetrics,
  getTests, getJobsStatus, getProjectSignals, getProjectStates, postTest, postRootAggregation,
  postSessionsRun, postSignalTest, postSignalsAggregation, postStateTest, postStatesAggregation, postUserSessionsRun,
  postUsersAggregation
} from '../../../../api.js'
import { loadSessions, sessions, sessionsLoading } from '../../../../chatStore.js'
import { confirmDialog } from '../../../../dialogStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

// Lets EditProjectView.vue mirror this panel's own selection into the
// Inspector's read-only Info tab — this stays the single source of truth
// for selectedNodeId, the parent just gets told when it changes.
const emit = defineEmits(['select'])

// Applies to whichever node gets activated next — one shared control, not a per-node choice.
const strategy = ref('batch')
const strategyLabels = { batch: 'Batch', turn_by_turn: 'Turn-by-turn' }

const strategyOpen = ref(false)
const strategyBtnEl = ref(null)
const strategyPanelEl = ref(null)
const strategyPanelStyle = ref({})
const strategyLabel = computed(() => strategyLabels[strategy.value])

// Running total of AI tokens consumed so far — piggybacked onto every
// SSE test-event message by the backend's QueueProgressBroadcaster (see
// AiService.get_total_tokens), not fetched separately.
const tokensBurnt = ref(0)

async function positionStrategyPanel() {
  await nextTick()
  const btn = strategyBtnEl.value
  if (!btn) return
  const btnRect = btn.getBoundingClientRect()
  strategyPanelStyle.value = { left: `${btnRect.left}px`, top: `${btnRect.bottom + 4}px` }
}

async function toggleStrategyMenu() {
  strategyOpen.value = !strategyOpen.value
  if (strategyOpen.value) await positionStrategyPanel()
}

function closeStrategyMenu() {
  strategyOpen.value = false
}

function selectStrategy(value) {
  strategy.value = value
  closeStrategyMenu()
}

function handleStrategyClickOutside(event) {
  if (!strategyOpen.value) return
  if (strategyBtnEl.value?.contains(event.target)) return
  if (strategyPanelEl.value?.contains(event.target)) return
  closeStrategyMenu()
}
document.addEventListener('click', handleStrategyClickOutside, true)
window.addEventListener('resize', closeStrategyMenu)
window.addEventListener('scroll', closeStrategyMenu, true)

const treeWidth = ref(280)
let draggingTree = false

function startTreeDrag(event) {
  draggingTree = true
  event.preventDefault()
}

function onTreeDrag(event) {
  if (!draggingTree) return
  treeWidth.value = Math.min(480, Math.max(200, treeWidth.value + event.movementX))
}

function stopTreeDrag() {
  draggingTree = false
}

const projectStates = ref([])
const statesLoading = ref(false)

const projectSignals = ref([])
const signalsLoading = ref(false)

// name -> {ui_label, ui_description} for the fixed core-benchmark-metric
// registry (state_accuracy, signal_accuracy, ...) — every result row's own
// `name` below is one of these, resolved for display instead of the raw
// identifier. Loaded once; this registry is static per backend build, not
// per-project data.
const metricDefinitions = ref({})

async function loadMetricDefinitions() {
  try {
    const metrics = await getTestMetrics(props.projectName)
    metricDefinitions.value = Object.fromEntries(metrics.map((m) => [m.name, m]))
  } catch {
    // already surfaced via apiFetch
  }
}

function metricLabel(name) {
  return metricDefinitions.value[name]?.ui_label ?? name
}

function metricDescription(name) {
  return metricDefinitions.value[name]?.ui_description ?? null
}

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
// different source) from the SSE status stream above, so it stays separate.
const nodeLastResult = ref({})

const selectedNodeId = ref(null)
const selectedRun = ref(null)
const selectedRunLoading = ref(false)

// completed with no error -> ok; completed but error carries text (one
// or more sessions skipped, e.g. no known starting state) -> warning,
// never a threshold on the metrics themselves. failed -> fail.
function statusFromOutcome(status, error) {
  if (status === 'failed') return 'fail'
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

// The project-wide "run everything" control lives in this panel's own
// header (styled like the reset button next to it), not in TestsTree —
// 'root' has no row of its own in the tree any more.
const rootStatus = computed(() => currentStrategyStatuses.value.root ?? 'idle')
const rootBusy = computed(() => ['pending', 'ready', 'running', 'paused'].includes(rootStatus.value))
// See TestNodeButton's own buttonState: 'running' (actively being
// executed right now) is the only busy state that reads green — every
// other in-flight state ('pending'/'ready'/'paused') stays blue.
const rootButtonState = computed(() => (rootStatus.value === 'running' ? 'running' : (rootBusy.value ? 'ready' : rootStatus.value)))

const selectedCacheKey = computed(() => (
  selectedNodeId.value ? cacheKey(strategy.value, selectedNodeId.value) : null
))

const selectedNodeError = computed(() => {
  const message = nodeEvents.value[selectedCacheKey.value]
  return message?.job_status === 'failed' ? message.error : null
})

// Writes one node's event as a single, complete replacement — used both
// for real SSE messages (job_status/queue_status straight from the
// backend, see JobQueue._broadcast_status) and for the optimistic
// 'running'/'completed'/'failed' the activate*() functions below set on
// click, before the first real one arrives — jobStatus here is simple
// on purpose, so it's translated into the same two-field shape a real
// message carries, and outcome() never needs to special-case its origin.
function setNodeEvent(key, jobStatus, error = null) {
  const queueStatus = jobStatus === 'completed' || jobStatus === 'failed' ? 'exited' : jobStatus === 'running' ? 'running' : 'ready'
  nodeEvents.value = { ...nodeEvents.value, [key]: { key, job_status: jobStatus, queue_status: queueStatus, percentage: null, error } }
}

// nodeId's own {kind, target} in the aggregate-result/jobs-status
// vocabulary — null for 'session:*' and 'root', neither of which is one.
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

function nodeIdFor(kind, target) {
  if (kind === 'state') return `state:${target}`
  if (kind === 'signal') return `signal:${target}`
  if (kind === 'user_sessions') return `user:${target}`
  if (kind === 'sessions') return 'sessions-branch'
  if (kind === 'users') return 'users-branch'
  if (kind === 'all_states') return 'states-branch'
  if (kind === 'all_signals') return 'signals-branch'
  return null
}

async function fetchAggregateResult(key, eventStrategy, kind, target) {
  try {
    const result = await getAggregateResult(props.projectName, kind, target, eventStrategy)
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
  if (typeof message.tokens === 'number') tokensBurnt.value = message.tokens
  nodeEvents.value = { ...nodeEvents.value, [message.key]: message }

  const { key, job_status: status, queue_status: queueStatus } = message
  const separatorIndex = key.indexOf(':')
  const eventStrategy = key.slice(0, separatorIndex)
  const nodeId = key.slice(separatorIndex + 1)
  if (nodeId.startsWith('session:')) {
    if (selectedNodeId.value === nodeId && strategy.value === eventStrategy) loadSelectedRun(nodeId)
    return
  }
  if (queueStatus !== 'exited' || status !== 'completed') return
  const target = aggregateKindAndTarget(nodeId)
  if (target == null) return // root — no result of its own
  fetchAggregateResult(key, eventStrategy, target.kind, target.target)
}

async function hydrateJobsStatus() {
  let jobsStatus = null
  try {
    jobsStatus = await getJobsStatus(props.projectName, strategy.value)
  } catch {
    return
  }
  for (const { session_id, status } of jobsStatus.sessions) {
    if (status === 'ok') setNodeEvent(cacheKey(strategy.value, `session:${session_id}`), 'completed')
  }
  for (const { kind, target, status } of jobsStatus.aggregates) {
    if (status !== 'ok') continue
    const nodeId = nodeIdFor(kind, target)
    const key = cacheKey(strategy.value, nodeId)
    setNodeEvent(key, 'completed')
    fetchAggregateResult(key, strategy.value, kind, target)
  }
}

async function activateSessionLeaf(nodeId, activeStrategy) {
  const key = cacheKey(activeStrategy, nodeId)
  setNodeEvent(key, 'running')
  try {
    const sessionId = Number(nodeId.slice('session:'.length))
    await postTest(props.projectName, sessionId, activeStrategy)
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
    await postStateTest(props.projectName, stateKey, activeStrategy)
  } catch {
    // already surfaced via apiFetch
    setNodeEvent(key, 'failed')
  }
}

async function activateSessionsRun(activeStrategy) {
  const key = cacheKey(activeStrategy, 'sessions-branch')
  setNodeEvent(key, 'running')
  try {
    await postSessionsRun(props.projectName, activeStrategy)
  } catch {
    // already surfaced via apiFetch
    setNodeEvent(key, 'failed')
  }
}

async function activateAllStates(activeStrategy) {
  const key = cacheKey(activeStrategy, 'states-branch')
  setNodeEvent(key, 'running')
  try {
    await postStatesAggregation(props.projectName, activeStrategy)
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
    await postSignalTest(props.projectName, signalName, activeStrategy)
  } catch {
    // already surfaced via apiFetch
    setNodeEvent(key, 'failed')
  }
}

async function activateAllSignals(activeStrategy) {
  const key = cacheKey(activeStrategy, 'signals-branch')
  setNodeEvent(key, 'running')
  try {
    await postSignalsAggregation(props.projectName, activeStrategy)
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
    await postUserSessionsRun(props.projectName, username, activeStrategy)
  } catch {
    // already surfaced via apiFetch
    setNodeEvent(key, 'failed')
  }
}

async function activateUsersAggregation(activeStrategy) {
  const key = cacheKey(activeStrategy, 'users-branch')
  setNodeEvent(key, 'running')
  try {
    await postUsersAggregation(props.projectName, activeStrategy)
  } catch {
    // already surfaced via apiFetch
    setNodeEvent(key, 'failed')
  }
}

async function activateRoot(activeStrategy) {
  const key = cacheKey(activeStrategy, 'root')
  setNodeEvent(key, 'running')
  try {
    await postRootAggregation(props.projectName, activeStrategy)
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

function onActivateRoot() {
  if (rootBusy.value) return
  onActivate('root')
}

async function loadSelectedRun(nodeId) {
  const sessionId = Number(nodeId.slice('session:'.length))
  selectedRunLoading.value = true
  try {
    const runs = await getTests(props.projectName, sessionId)
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
  if (nodeId === 'root') return props.projectName
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

function formatNumber(value) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

const resettingCache = ref(false)

const anyTestExecuted = computed(() => Object.keys(nodeEvents.value).length > 0)

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
    await deleteTests(props.projectName)
    nodeEvents.value = {}
    nodeLastResult.value = {}
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

let testEventSource = null

onMounted(() => {
  // selectedNodeId always starts null on a fresh mount (this tab isn't
  // kept alive while closed — see EditProjectView.vue's autoOpen v-if),
  // so there's never anything already selected to defer to here.
  onSelect('root')
  loadSessions(true, props.projectName)
  loadMetricDefinitions()
  hydrateJobsStatus()
  statesLoading.value = true
  getProjectStates(props.projectName).then((states) => {
    projectStates.value = states
  }).catch(() => {
    // already surfaced via apiFetch
  }).finally(() => {
    statesLoading.value = false
  })
  signalsLoading.value = true
  getProjectSignals(props.projectName).then(({ signals }) => {
    projectSignals.value = signals.map((entry) => entry.signal)
  }).catch(() => {
    // already surfaced via apiFetch
  }).finally(() => {
    signalsLoading.value = false
  })
  testEventSource = createTestEventsSource(props.projectName)
  testEventSource.onmessage = (event) => handleTestEvent(JSON.parse(event.data))
  window.addEventListener('mousemove', onTreeDrag)
  window.addEventListener('mouseup', stopTreeDrag)
})

onBeforeUnmount(() => {
  testEventSource?.close()
  window.removeEventListener('mousemove', onTreeDrag)
  window.removeEventListener('mouseup', stopTreeDrag)
  document.removeEventListener('click', handleStrategyClickOutside, true)
  window.removeEventListener('resize', closeStrategyMenu)
  window.removeEventListener('scroll', closeStrategyMenu, true)
})
</script>

<template>
  <div class="project-test-panel">
    <div class="tests-panel-tree" :style="{ width: treeWidth + 'px' }">
      <div class="tests-panel-strategy">
        <span class="tests-panel-tree-header">Test explorer</span>
        <div class="tests-panel-tree-actions">
          <button
            type="button"
            class="tests-panel-root-btn"
            :class="`tests-panel-root-btn-${rootButtonState}`"
            :disabled="rootBusy"
            :title="rootStatus === 'pending' || rootStatus === 'ready' ? 'Queued…' : rootStatus === 'paused' ? 'Paused…' : rootStatus === 'running' ? 'Running…' : 'Run test'"
            @click="onActivateRoot"
          >
            <!-- Idle/ok/warning/fail: same chrome as the reset button next
                 to it (bordered square, plain icon) — the ring only earns
                 its place while something is actually in flight. -->
            <svg v-if="rootStatus === 'idle'" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
            <svg v-else-if="rootBusy" viewBox="0 0 24 24" width="16" height="16">
              <circle
                class="tests-panel-root-btn-ring"
                :class="{ 'tests-panel-root-btn-spinner-indeterminate': rootStatus !== 'running' }"
                cx="12" cy="12" r="10" pathLength="100" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"
                :stroke-dasharray="rootStatus === 'running' ? '8 100' : '50 100'"
              />
              <g v-if="rootStatus === 'paused'" transform="translate(12 12) scale(0.6) translate(-12 -12)">
                <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" />
                <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" />
              </g>
              <!-- 'running': a worker is inside root's own step right now
                   (root has exactly one — see RootAggregationJob._prepare)
                   — the lightning marks that instant, not "root is always
                   high-priority". Drawn inside the same ring as the spinner,
                   not swapped in place of it, so the ring stays visible. -->
              <path
                v-else-if="rootStatus === 'running'"
                class="tests-panel-root-btn-lightning"
                d="M11 21v-8H7l6-11v8h4l-6 11z"
                transform="translate(12 12) scale(0.75) translate(-12 -12)"
                fill="currentColor"
              />
            </svg>
            <svg v-else-if="rootStatus === 'ok'" viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline class="tests-panel-root-btn-check" points="4 13 9 18 20 6" pathLength="100" />
            </svg>
            <svg v-else-if="rootStatus === 'warning'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
              <path d="M12 3L1 21h22L12 3zm0 5.5l6.6 11.5H5.4L12 8.5zM11 11v4h2v-4h-2zm0 5v2h2v-2h-2z" />
            </svg>
            <svg v-else-if="rootStatus === 'fail'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
              <path d="M12 2a10 10 0 100 20 10 10 0 000-20zm3.5 13.1L15.1 16.5 12 13.4l-3.1 3.1-1.4-1.4L10.6 12 7.5 8.9l1.4-1.4L12 10.6l3.1-3.1 1.4 1.4L13.4 12z" />
            </svg>
          </button>
          <button
            class="tests-panel-reset-btn"
            title="Reset test cache"
            :disabled="resettingCache || !anyTestExecuted"
            @click="onResetCache"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
              <path d="M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z" />
            </svg>
          </button>
        </div>
      </div>
      <p v-if="sessionsLoading || statesLoading || signalsLoading" class="tests-panel-tree-status">Loading…</p>
      <TestsTree
        v-else
        :sessions="sessions"
        :states="projectStates"
        :signals="projectSignals"
        :statuses="currentStrategyStatuses"
        :progresses="currentStrategyProgress"
        :selected-node-id="selectedNodeId"
        @select="onSelect"
        @activate="onActivate"
      />
    </div>

    <div class="tests-panel-split-divider" @mousedown="startTreeDrag"></div>

    <div class="tests-panel-main">
      <div class="tests-panel-toolbar">
        <div class="tests-panel-toolbar-title">
          <span class="tests-panel-tokens-label">Tokens burnt: {{ tokensBurnt }}</span>
        </div>
        <div class="tests-panel-toolbar-actions">
          <div class="strategy-menu">
            <button ref="strategyBtnEl" class="strategy-btn" :title="strategyLabel" @click="toggleStrategyMenu">
              <span class="strategy-btn-label">{{ strategyLabel }}</span>
              <span class="strategy-btn-caret">▾</span>
            </button>
            <Teleport to="body">
              <div v-if="strategyOpen" ref="strategyPanelEl" class="model-panel" :style="strategyPanelStyle">
                <ul class="model-list">
                  <li v-for="(label, value) in strategyLabels" :key="value">
                    <button class="model-item" @click="selectStrategy(value)">
                      <span class="model-item-check">{{ strategy === value ? '✓' : '' }}</span>
                      <span class="model-item-label">{{ label }}</span>
                    </button>
                  </li>
                </ul>
              </div>
            </Teleport>
          </div>
        </div>
      </div>
      <div class="tests-panel-content">
      <p v-if="!selectedNodeId" class="tests-panel-placeholder">Select a node to see its results.</p>

      <template v-else-if="selectedNodeId === 'root'">
        <p class="tests-panel-placeholder">Aggregates not available in this product version.<br />Please select Sessions, States, Users, or Signals to see results.</p>
      </template>

      <template v-else-if="selectedNodeId.startsWith('session:')">
        <p v-if="selectedRunLoading" class="tests-panel-placeholder">Loading…</p>
        <p v-else-if="!selectedRun" class="tests-panel-placeholder">
          No test has been run for this session under this strategy yet.
        </p>
        <p v-else-if="selectedRun.status === 'failed'" class="tests-panel-error">{{ selectedRun.error || 'Test failed.' }}</p>
        <p v-else-if="selectedRun.status !== 'completed'" class="tests-panel-placeholder">Test {{ selectedRun.status }}…</p>
        <template v-else>
          <p v-if="selectedRun.error" class="tests-panel-error">{{ selectedRun.error }}</p>
          <p v-if="selectedRun.stale" class="tests-panel-stale-warning">
            The project has changed since this test ran — results may not reflect the current version.
          </p>
          <table v-if="selectedRun.results && selectedRun.results.length" class="tests-panel-metrics-table">
            <thead>
              <tr>
                <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="metric in selectedRun.results" :key="metric.name" :class="{ 'tests-panel-row-empty': !metric.sample_count }">
                <td><MetricDetail :label="metricLabel(metric.name)" :description="metricDescription(metric.name)" :value="metric.value" :median="metric.median" /></td>
                <td>{{ formatNumber(metric.standard_deviation) }}</td>
                <td>{{ metric.sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'sessions-branch' || selectedNodeId.startsWith('user:') || selectedNodeId === 'users-branch'">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey] || !nodeLastResult[selectedCacheKey].length" class="tests-panel-placeholder">
          No test has been run for {{ selectedNodeId.startsWith('user:') ? 'this user' : selectedNodeId === 'users-branch' ? 'the users aggregation' : 'the whole project' }} under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="metric in nodeLastResult[selectedCacheKey]" :key="metric.name"
              :class="{ 'tests-panel-row-empty': !metric.sample_count }"
            >
              <td><MetricDetail :label="metricLabel(metric.name)" :description="metricDescription(metric.name)" :value="metric.value" :median="metric.median" /></td>
              <td>{{ formatNumber(metric.standard_deviation) }}</td>
              <td>{{ metric.sample_count }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-else-if="selectedNodeId.startsWith('state:')">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No test has been run for this state under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr>
                <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td><MetricDetail :label="metricLabel(nodeLastResult[selectedCacheKey].name)" :description="metricDescription(nodeLastResult[selectedCacheKey].name)" :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median" /></td>
                <td>{{ formatNumber(nodeLastResult[selectedCacheKey].standard_deviation) }}</td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId.startsWith('signal:')">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No test has been run for this signal under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr>
                <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td>
                  <MetricDetail
                    :label="metricLabel('signal_accuracy')" :description="metricDescription('signal_accuracy')"
                    :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median"
                  />
                </td>
                <td>{{ formatNumber(nodeLastResult[selectedCacheKey].standard_deviation) }}</td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'signals-branch'">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No signal test has been run under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr><th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Samples</th></tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td><MetricDetail label="Overall" :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median" /></td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'states-branch'">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No state test has been run under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr><th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Samples</th></tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td><MetricDetail label="Overall" :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median" /></td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <p v-else class="tests-panel-placeholder">{{ selectedNodeLabel }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.project-test-panel {
  flex: 1;
  display: flex;
  flex-direction: row;
  min-height: 0;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.tests-panel-tree {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
}

.tests-panel-split-divider {
  flex-shrink: 0;
  width: 6px;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}
.tests-panel-split-divider:hover {
  background: #dbe4f0;
}

.tests-panel-strategy {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid #ddd;
}

.tests-panel-tree-header {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.tests-panel-tree-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.tests-panel-root-btn,
.tests-panel-reset-btn {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
}

.tests-panel-root-btn,
.tests-panel-reset-btn {
  border: 1px solid #4a6fa5;
}

.tests-panel-root-btn:hover:not(:disabled),
.tests-panel-reset-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.tests-panel-root-btn:disabled,
.tests-panel-reset-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tests-panel-root-btn-ready {
  border-color: transparent;
  background: none;
  color: #4a6fa5;
}

.tests-panel-root-btn-running {
  border-color: transparent;
  background: none;
  color: #2e7d32;
}

.tests-panel-root-btn-lightning {
  animation: tests-panel-root-btn-glow 0.9s ease-in-out infinite alternate;
}

@keyframes tests-panel-root-btn-glow {
  from { opacity: 1; }
  to { opacity: 0.3; }
}

.tests-panel-root-btn-ring {
  transform-origin: center;
  transform: rotate(-90deg);
  transition: stroke-dasharray 0.2s linear;
}

.tests-panel-root-btn-ring.tests-panel-root-btn-spinner-indeterminate {
  animation: tests-panel-root-btn-spin 0.9s linear infinite;
}

@keyframes tests-panel-root-btn-spin {
  from { transform: rotate(-90deg); }
  to { transform: rotate(270deg); }
}

.tests-panel-root-btn-ok {
  border-color: #2e7d32;
  color: #2e7d32;
}

.tests-panel-root-btn-check {
  stroke-dasharray: 100;
  stroke-dashoffset: 100;
  animation: tests-panel-root-btn-check-draw 0.4s ease-out forwards;
}

@keyframes tests-panel-root-btn-check-draw {
  to { stroke-dashoffset: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .tests-panel-root-btn-check {
    animation: none;
    stroke-dashoffset: 0;
  }
}

.tests-panel-root-btn-ok:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.tests-panel-root-btn-warning {
  color: #b26a00;
}

.tests-panel-root-btn-warning:hover:not(:disabled) {
  background: #b26a00;
  color: white;
}

.tests-panel-root-btn-fail {
  color: #c62828;
}

.tests-panel-root-btn-fail:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.tests-panel-tokens-label {
  font-size: 0.8rem;
  color: #555;
  white-space: nowrap;
}

.strategy-menu {
  position: relative;
  display: flex;
  align-items: center;
}

.strategy-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  max-width: 160px;
}

.strategy-btn-label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.strategy-btn-caret {
  flex: none;
  font-size: 0.65rem;
}

.strategy-btn:hover {
  background: #4a6fa5;
  color: white;
}

.tests-panel-tree-status {
  padding: 0.75rem 0.9rem;
  font-size: 0.85rem;
  color: #666;
}

.tests-panel-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.tests-panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.tests-panel-toolbar-title {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.tests-panel-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tests-panel-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.tests-panel-distribution-block {
  width: 100%;
  height: 200px;
  max-height: 200px;
  margin-bottom: 1rem;
}

.tests-panel-metric-header {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.tests-panel-placeholder {
  color: #666;
}

.tests-panel-stale-warning {
  margin: 0 0 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #fff4e0;
  border: 1px solid #f0c674;
  color: #7a5300;
  font-size: 0.82rem;
}

.tests-panel-error {
  margin: 0 0 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #fde8e8;
  border: 1px solid #f0a8a8;
  color: #8a1f1f;
  font-size: 0.82rem;
  white-space: pre-wrap;
  word-break: break-word;
}

.tests-panel-metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}

.tests-panel-metrics-table th,
.tests-panel-metrics-table td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #eee;
}

.tests-panel-metrics-table th {
  color: #555;
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.7rem;
  letter-spacing: 0.03em;
}

.tests-panel-overall-row td {
  font-weight: 600;
  border-top: 2px solid #ccc;
}

.tests-panel-row-empty td {
  color: #aaa;
}
</style>
