<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import Inspector from './inspector/Inspector.vue'
import {
  getMessages, getSessionSignals, getSessions, putMessageExpectedState, putMessageExpectedSignals,
  deleteSessionAnnotations
} from '../api.js'
import { currentSessionId, sessions, sessionsLoading, loadSessions, selectSession } from '../chatStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close'])

const loading = ref(true)
// Raw backend rows (id, role, content, audio_text, timestamp,
// session_id) — see db.get_messages. Kept as-is (not chatStore.js's live
// `messages` shape) since this view reviews a fixed past session, never
// the live conversation.
const rawMessages = ref([])
// The session's full Signals event log (id, timestamp, values,
// expected_values, expected_state, old_state, action, new_state,
// message_id) — see db.get_signals — from which the timeline's
// transition rows, every point-in-time signal-values reconstruction, and
// every annotation (see annotatableSignalsRow) are derived, with no
// further backend round trips.
const signalsLog = ref([])
const sessionStartState = ref(null)

const inspectorRef = ref(null)
const inspectorWidth = ref(360)
// The Sessions panel starts open — reviewing a specific session is the
// point of this view, so the picker should always be immediately visible
// rather than tucked behind a toggle.
const benchmarkSessionsPanelOpen = ref(true)
const sessionsPanelWidth = ref(240)
let dragTarget = null

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function startSessionsDrag(event) {
  dragTarget = 'sessions'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget === 'inspector') {
    inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
    inspectorRef.value?.resize()
  } else if (dragTarget === 'sessions') {
    sessionsPanelWidth.value = Math.min(420, Math.max(160, sessionsPanelWidth.value + event.movementX))
  }
}

function stopDrag() {
  dragTarget = null
}

// Toggled by the header's own Sessions button, same as ChatWindow.vue's
// own panel — but a local, independent open/closed flag: the main page's
// own Sessions panel (see chatStore.js's sessionsPanelOpen) is a separate
// piece of UI, hidden behind this full-screen overlay while it's open,
// and shouldn't change just because this view's own panel did.
function toggleBenchmarkSessionsPanel() {
  benchmarkSessionsPanelOpen.value = !benchmarkSessionsPanelOpen.value
  if (benchmarkSessionsPanelOpen.value) loadSessions()
}

// Picking a session here uses the exact same shared mechanism as every
// other session picker in the app (see chatStore.js's own selectSession,
// used by SessionsPanel.vue's other callers) — currentSessionId is the
// one source of truth, and the watcher below reacts to it changing.
function onSelectSession(session) {
  selectSession(session)
}

function handleWindowResize() {
  inspectorRef.value?.resize()
}

async function loadTimeline() {
  const sessionId = currentSessionId.value
  if (sessionId == null) {
    rawMessages.value = []
    signalsLog.value = []
    sessionStartState.value = null
    loading.value = false
    return
  }
  loading.value = true
  selected.value = null
  try {
    const [messageRows, signalRows, allSessions] = await Promise.all([
      getMessages(sessionId),
      getSessionSignals(sessionId),
      getSessions()
    ])
    rawMessages.value = messageRows
    signalsLog.value = signalRows
    sessionStartState.value = allSessions.find((s) => s.id === sessionId)?.start_state ?? null
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

// A session switch (from this view's own Sessions panel, or the main
// page's — currentSessionId is shared, see onSelectSession) always shows
// *that* session's own timeline from scratch — whatever was selected
// before belonged to a different session's history.
watch(currentSessionId, loadTimeline)

function toBubbleMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, timestamp: m.timestamp }
}

// Whether the Signals row this message's own evaluation produced (see
// Signals.message_id) has at least one expert-annotated expected signal
// value — drives the message bubble's own "!" marker (see MessageBubble.
// vue's signalsAnnotated prop). Independent of the transition ✓/✕
// indicator, which is about expected_state, not expected_values.
function messageHasAnnotatedSignals(message) {
  const row = signalsLog.value.find((s) => s.message_id === message.id)
  if (!row?.expected_values) return false
  try {
    const parsed = JSON.parse(row.expected_values)
    return parsed != null && Object.keys(parsed).length > 0
  } catch {
    return false
  }
}

// A transition linked to a message (see Signals.message) is positioned as
// if it happened exactly when that message did, not by its own raw
// timestamp: auto-tracking's own evaluation for a user message runs
// *before* that message is saved (see backend ChatService.
// _process_turn_locked), so the transition's own row can end up
// timestamped slightly earlier than the very message that caused it —
// which would otherwise show the state change before the message that
// produced it. A manual action's transition (no linked message) has
// nothing to correct against, so it keeps its own raw timestamp.
function effectiveTimestamp(entry) {
  if (entry.kind === 'message') return entry.message.timestamp
  const messageId = entry.transition.message_id
  const linkedMessage = messageId != null ? rawMessages.value.find((m) => m.id === messageId) : null
  return linkedMessage ? linkedMessage.timestamp : entry.transition.timestamp
}

// Whether a transition's own expert-annotated expected_state (see
// Signals.expected_state — lives directly on the transition's own row
// now, no message lookup needed) agrees with what actually happened —
// null when unannotated (the timeline shows no verdict either way, same
// as the Inspector's own States tab).
function transitionAnnotationStatus(transition) {
  if (transition.expected_state == null) return null
  return transition.expected_state === transition.new_state ? 'correct' : 'incorrect'
}

// Chronological, merged view of the session's messages and its real
// (non-self-loop) state transitions — a self-loop has nothing to show
// (the state didn't visibly change), same exclusion db.get_last_transition_timestamp
// already applies for history-cutoff purposes.
const timeline = computed(() => {
  const messageEntries = rawMessages.value.map((m) => ({ kind: 'message', timestamp: m.timestamp, message: m }))
  const transitionEntries = signalsLog.value
    .filter((s) => s.new_state != null && s.new_state !== s.old_state)
    .map((s) => ({
      kind: 'transition',
      timestamp: s.timestamp,
      transition: s,
      annotationStatus: transitionAnnotationStatus(s)
    }))
  return [...messageEntries, ...transitionEntries].sort((a, b) => {
    const ta = effectiveTimestamp(a)
    const tb = effectiveTimestamp(b)
    if (ta !== tb) return ta.localeCompare(tb)
    // The same effective moment only happens when a transition is tied to
    // this exact message (see effectiveTimestamp) — the message it's
    // explaining always reads first.
    return (a.kind === 'message' ? 0 : 1) - (b.kind === 'message' ? 0 : 1)
  })
})

// The point in time currently reflected by the Inspector — a message or a
// transition clicked in the timeline (see selectMessage/selectTransition).
// null until the first click, showing just the project's own definitions
// with nothing highlighted.
const selected = ref(null)

function isMessageSelected(message) {
  return selected.value?.kind === 'message' && selected.value.message.id === message.id
}

function isTransitionSelected(transition) {
  return selected.value?.kind === 'transition' && selected.value.transition.id === transition.id
}

function selectMessage(message) {
  selected.value = { kind: 'message', message }
}

function selectTransition(transition) {
  selected.value = { kind: 'transition', transition }
}

// The state active immediately after the latest transition at or before
// `timestamp`, or the session's own starting state if none has fired yet.
function stateAsOf(timestamp) {
  let result = sessionStartState.value
  for (const entry of timeline.value) {
    if (entry.kind !== 'transition' || entry.timestamp > timestamp) continue
    result = entry.transition.new_state
  }
  return result
}

// The latest Signals row that actually carries values (a plain snapshot,
// or a transition that had signal_values — see db.py's Signals.values) at
// or before `timestamp`, reshaped into Inspector's own
// { [name]: { value, error } } signalValues prop shape (see
// AutoTracker.run — a persisted snapshot is always a plain {name: value}
// dict, never {value, error}, so error is always null here).
function signalValuesAsOf(timestamp) {
  let latest = null
  for (const row of signalsLog.value) {
    if (row.timestamp > timestamp || row.values == null) continue
    if (latest == null || row.timestamp >= latest.timestamp) latest = row
  }
  if (!latest) return {}
  const parsed = JSON.parse(latest.values)
  return Object.fromEntries(Object.entries(parsed).map(([name, value]) => [name, { value, error: null }]))
}

// A transition has no message_id of its own, but point-in-time metrics
// can only be pinned to one (see api.js's getMetrics/chat_service.py's
// get_metrics — "Message.timestamp", per the message_id it resolves
// internally) — so a transition's own metrics use whichever message most
// recently preceded it.
function nearestMessageIdAtOrBefore(timestamp) {
  let result = null
  for (const m of rawMessages.value) {
    if (m.timestamp > timestamp) break
    result = m.id
  }
  return result
}

const highlightedStateKey = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'transition') return selected.value.transition.new_state
  return stateAsOf(selected.value.message.timestamp)
})

// Only a transition has "the action that produced it" to highlight — the
// very first transition's own old_state is "" (see db.py's ChatSessionManager
// init), which isn't a real graph node, so it's left unhighlighted.
const firedActionEdge = computed(() => {
  if (selected.value?.kind !== 'transition') return null
  const t = selected.value.transition
  return t.old_state ? { stateKey: t.old_state, actionName: t.action } : null
})

const untilMessageId = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'message') return selected.value.message.id
  // A transition auto-tracking produced is linked straight back to the
  // message whose evaluation caused it (see db.py's Signals.message) — an
  // exact lookup, only falling back to the nearest-before heuristic for a
  // manual action's transition, which was never evaluated from any
  // message at all.
  return selected.value.transition.message_id ?? nearestMessageIdAtOrBefore(selected.value.transition.timestamp)
})

const signalValues = computed(() => {
  if (!selected.value) return {}
  const timestamp = selected.value.kind === 'message' ? selected.value.message.timestamp : selected.value.transition.timestamp
  return signalValuesAsOf(timestamp)
})

// The Signals row backing the current selection's own evaluation, if
// any — the row itself for a clicked transition auto-tracking produced
// (see its own message_id), or (found by message_id) the row a clicked
// message's own evaluation produced. null when there's no evaluation to
// annotate against at all — a manual action's transition (message_id
// null: see project_service.apply_manual_action), or a message
// auto-tracking never evaluated anything after (see Signals.message's own
// docstring) — the Inspector's annotation controls only ever show for a
// non-null value here.
const annotatableSignalsRow = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'transition') {
    return selected.value.transition.message_id != null ? selected.value.transition : null
  }
  return signalsLog.value.find((s) => s.message_id === selected.value.message.id) ?? null
})

// The message id to PUT an annotation change against — the annotation
// API is message-centric (see api.js's putMessageExpectedState/
// putMessageExpectedSignals), so a transition selection still resolves
// back to whichever message its own row says caused it.
const annotatableMessageId = computed(() => {
  if (!annotatableSignalsRow.value) return null
  return selected.value.kind === 'message' ? selected.value.message.id : annotatableSignalsRow.value.message_id
})

const expectedState = computed(() => annotatableSignalsRow.value?.expected_state ?? null)
const expectedValues = computed(() => {
  const raw = annotatableSignalsRow.value?.expected_values
  return raw ? JSON.parse(raw) : {}
})

async function onUpdateExpectedState(value) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    const updated = await putMessageExpectedState(messageId, value)
    const idx = signalsLog.value.findIndex((s) => s.id === updated.id)
    if (idx !== -1) signalsLog.value[idx] = { ...signalsLog.value[idx], expected_state: updated.expected_state }
    inspectorRef.value?.refreshPerformance()
  } catch {
    // already surfaced via apiFetch
  }
}

async function onUpdateExpectedSignals(values) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    const updated = await putMessageExpectedSignals(messageId, values)
    const idx = signalsLog.value.findIndex((s) => s.id === updated.id)
    if (idx !== -1) signalsLog.value[idx] = { ...signalsLog.value[idx], expected_values: updated.expected_values }
    inspectorRef.value?.refreshPerformance()
  } catch {
    // already surfaced via apiFetch
  }
}

// Whether this session has anything for "Unlabel all" to actually clear —
// disables the button rather than opening a confirm dialog for nothing.
const hasAnyAnnotations = computed(() => {
  return signalsLog.value.some((s) => s.expected_state != null || s.expected_values != null)
})

const unlabelingAll = ref(false)

async function onUnlabelAll() {
  if (!currentSessionId.value || !hasAnyAnnotations.value) return
  if (!window.confirm('Remove every annotation in this session? This cannot be undone.')) return
  unlabelingAll.value = true
  try {
    await deleteSessionAnnotations(currentSessionId.value)
    signalsLog.value = signalsLog.value.map((s) => ({ ...s, expected_state: null, expected_values: null }))
    inspectorRef.value?.refreshPerformance()
  } catch {
    // already surfaced via apiFetch
  } finally {
    unlabelingAll.value = false
  }
}

// Metrics aren't reactive to props on their own (see Inspector.vue's
// refreshMetrics docstring) — every selection change needs an explicit
// nudge, same as EditProjectView.vue's turnCount watcher.
watch(selected, () => {
  nextTick(() => inspectorRef.value?.refreshMetrics())
})

onMounted(() => {
  loadTimeline()
  // The Sessions panel starts open (see benchmarkSessionsPanelOpen) —
  // toggleBenchmarkSessionsPanel only loads on a closed-to-open flip, so
  // the initial open needs its own load.
  loadSessions()
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<template>
  <div class="benchmark-overlay">
    <div class="benchmark-header">
      <h2>Benchmark project — {{ projectName }}</h2>
      <div class="benchmark-header-actions">
        <button
          class="sessions-toggle-btn"
          :class="{ 'sessions-toggle-btn-on': benchmarkSessionsPanelOpen }"
          @click="toggleBenchmarkSessionsPanel"
        >
          Sessions
        </button>
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <div class="benchmark-body">
      <div class="benchmark-chat-pane">
        <Transition name="panel-slide-left">
          <div v-if="benchmarkSessionsPanelOpen" class="sessions-panel-wrap">
            <div class="sessions-panel" :style="{ width: sessionsPanelWidth + 'px' }">
              <SessionsPanel
                :sessions="sessions"
                :loading="sessionsLoading"
                :current-session-id="currentSessionId"
                :allow-create="false"
                :allow-delete="false"
                @select="onSelectSession"
              />
            </div>
            <div class="split-divider" @mousedown="startSessionsDrag"></div>
          </div>
        </Transition>

        <div class="benchmark-chat-content">
          <div class="benchmark-chat-toolbar">
            <span class="benchmark-chat-title">Chat</span>
            <button
              type="button"
              class="benchmark-unlabel-all-btn"
              :disabled="!hasAnyAnnotations || unlabelingAll"
              @click="onUnlabelAll"
            >
              {{ unlabelingAll ? 'Unlabelling…' : 'Unlabel all' }}
            </button>
          </div>

          <p v-if="loading" class="benchmark-status">Loading…</p>
          <p v-else-if="!currentSessionId" class="benchmark-status">
            No session selected — pick one from the Sessions panel first.
          </p>
          <p v-else-if="!timeline.length" class="benchmark-status">This session has no messages yet.</p>

          <div v-else class="benchmark-timeline">
            <template v-for="entry in timeline" :key="entry.kind + '-' + (entry.kind === 'message' ? entry.message.id : entry.transition.id)">
              <div
                v-if="entry.kind === 'message'"
                class="benchmark-row benchmark-message-row"
                :class="[
                  entry.message.role === 'user' ? 'benchmark-message-row-user' : 'benchmark-message-row-assistant',
                  { 'benchmark-row-selected': isMessageSelected(entry.message) }
                ]"
                @click="selectMessage(entry.message)"
              >
                <MessageBubble
                  :message="toBubbleMessage(entry.message)"
                  show-timestamp
                  :signals-annotated="messageHasAnnotatedSignals(entry.message)"
                />
              </div>

              <div
                v-else
                class="benchmark-row benchmark-transition-row"
                :class="[
                  { 'benchmark-row-selected': isTransitionSelected(entry.transition) },
                  entry.annotationStatus ? `benchmark-transition-row-${entry.annotationStatus}` : ''
                ]"
                @click="selectTransition(entry.transition)"
              >
                <span class="benchmark-transition-arrow">→</span>
                <span class="benchmark-transition-badge">{{ entry.transition.new_state }}</span>
                <span
                  v-if="entry.annotationStatus === 'correct'"
                  class="benchmark-transition-annotation-icon benchmark-transition-annotation-icon-correct"
                  title="Matches the expert-annotated expected state"
                >✓</span>
                <span
                  v-else-if="entry.annotationStatus === 'incorrect'"
                  class="benchmark-transition-annotation-icon benchmark-transition-annotation-icon-incorrect"
                  title="Differs from the expert-annotated expected state"
                >✕</span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

      <div class="benchmark-inspector-panel" :style="{ '--inspector-width': inspectorWidth + 'px' }">
        <Inspector
          ref="inspectorRef"
          :project-name="projectName"
          :highlighted-state-key="highlightedStateKey"
          :fired-action-edge="firedActionEdge"
          :signal-values="signalValues"
          :until-message-id="untilMessageId"
          :annotatable="annotatableSignalsRow != null"
          :expected-state="expectedState"
          :expected-values="expectedValues"
          :show-performance-tab="true"
          :benchmark-session-id="currentSessionId"
          :closable="false"
          @update-expected-state="onUpdateExpectedState"
          @update-expected-signals="onUpdateExpectedSignals"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.benchmark-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.benchmark-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.benchmark-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.benchmark-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
}

.sessions-toggle-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.sessions-toggle-btn:hover {
  background: #eef2f9;
}

.sessions-toggle-btn-on {
  background: #4a6fa5;
  color: white;
}

.sessions-toggle-btn-on:hover {
  background: #3d5c8a;
}

.benchmark-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.benchmark-chat-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: row;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.sessions-panel-wrap {
  display: flex;
  flex-direction: row;
  min-width: 0;
  min-height: 0;
}

.panel-slide-left-enter-active,
.panel-slide-left-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.panel-slide-left-enter-from,
.panel-slide-left-leave-to {
  opacity: 0;
  transform: translateX(-16px);
}

.sessions-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
}

.benchmark-chat-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.benchmark-chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

/* Same style as Inspector.vue's own .inspector-title. */
.benchmark-chat-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.benchmark-unlabel-all-btn {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.78rem;
}

.benchmark-unlabel-all-btn:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.benchmark-unlabel-all-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.benchmark-status {
  margin: auto;
  color: #444;
}

.benchmark-timeline {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.benchmark-row {
  display: flex;
  padding: 0.5rem 1rem;
  cursor: pointer;
}

.benchmark-row:hover {
  background: #f7f9fc;
}

.benchmark-row-selected {
  background: #e3ebf7;
}

.benchmark-message-row {
  justify-content: flex-start;
}

.benchmark-message-row-user {
  justify-content: flex-end;
}

.benchmark-message-row-assistant {
  justify-content: flex-start;
}

.benchmark-transition-row {
  justify-content: center;
  align-items: center;
  gap: 0.5rem;
  background: #fbf3e6;
}

.benchmark-transition-row:hover {
  background: #f6e9d2;
}

.benchmark-transition-row.benchmark-row-selected {
  background: #f0dcb0;
}

/* Whether the transition's own expert-annotated expected_state agrees
   with what actually happened (see transitionAnnotationStatus) — lets a
   reviewer spot a mismatch across the whole timeline at a glance, not
   just by opening the Inspector on each one. */
.benchmark-transition-row-correct {
  background: #e8f5e9;
}

.benchmark-transition-row-correct:hover {
  background: #dcefdd;
}

.benchmark-transition-row-correct.benchmark-row-selected {
  background: #c8e6c9;
}

.benchmark-transition-row-incorrect {
  background: #fdecea;
}

.benchmark-transition-row-incorrect:hover {
  background: #fbdedb;
}

.benchmark-transition-row-incorrect.benchmark-row-selected {
  background: #f5c6c2;
}

.benchmark-transition-arrow {
  color: #8a6d3b;
  font-weight: 600;
}

.benchmark-transition-badge {
  display: inline-block;
  padding: 0.15rem 0.7rem;
  border-radius: 999px;
  background: #4a6fa5;
  color: white;
  font-size: 0.78rem;
  font-weight: 600;
}

.benchmark-transition-annotation-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.2rem;
  height: 1.2rem;
  border-radius: 50%;
  font-size: 0.72rem;
  font-weight: 700;
  color: white;
}

.benchmark-transition-annotation-icon-correct {
  background: #2e7d32;
}

.benchmark-transition-annotation-icon-incorrect {
  background: #c62828;
}

.split-divider {
  flex-shrink: 0;
  width: 6px;
  margin: 0 0.4rem;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}

.split-divider:hover {
  background: #dbe4f0;
}

.benchmark-inspector-panel {
  flex-shrink: 0;
  width: var(--inspector-width);
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
