<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import Inspector from './Inspector.vue'
import { getMessages, getSessionSignals, getSessions } from '../api.js'
import { currentSessionId } from '../chatStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close'])

const loading = ref(true)
// Raw backend rows (id, role, content, audio_text, timestamp,
// expected_state, session_id) — see db.get_messages. Kept as-is (not
// chatStore.js's live `messages` shape) since this view reviews a fixed
// past session, never the live conversation.
const rawMessages = ref([])
// The session's full Signals event log (id, timestamp, values,
// expected_values, old_state, action, new_state) — see db.get_signals —
// from which both the timeline's transition rows and every point-in-time
// signal-values reconstruction below are derived, with no further
// backend round trips.
const signalsLog = ref([])
const sessionStartState = ref(null)

const inspectorRef = ref(null)
const inspectorWidth = ref(360)
let dragTarget = null

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget !== 'inspector') return
  inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
  inspectorRef.value?.resize()
}

function stopDrag() {
  dragTarget = null
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
  try {
    const [messageRows, signalRows, sessions] = await Promise.all([
      getMessages(sessionId),
      getSessionSignals(sessionId),
      getSessions()
    ])
    rawMessages.value = messageRows
    signalsLog.value = signalRows
    sessionStartState.value = sessions.find((s) => s.id === sessionId)?.start_state ?? null
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

function toBubbleMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, timestamp: m.timestamp }
}

// Chronological, merged view of the session's messages and its real
// (non-self-loop) state transitions — a self-loop has nothing to show
// (the state didn't visibly change), same exclusion db.get_last_transition_timestamp
// already applies for history-cutoff purposes.
const timeline = computed(() => {
  const messageEntries = rawMessages.value.map((m) => ({ kind: 'message', timestamp: m.timestamp, message: m }))
  const transitionEntries = signalsLog.value
    .filter((s) => s.new_state != null && s.new_state !== s.old_state)
    .map((s) => ({ kind: 'transition', timestamp: s.timestamp, transition: s }))
  return [...messageEntries, ...transitionEntries].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
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
  return nearestMessageIdAtOrBefore(selected.value.transition.timestamp)
})

const signalValues = computed(() => {
  if (!selected.value) return {}
  const timestamp = selected.value.kind === 'message' ? selected.value.message.timestamp : selected.value.transition.timestamp
  return signalValuesAsOf(timestamp)
})

// Metrics aren't reactive to props on their own (see Inspector.vue's
// refreshMetrics docstring) — every selection change needs an explicit
// nudge, same as EditProjectView.vue's turnCount watcher.
watch(selected, () => {
  nextTick(() => inspectorRef.value?.refreshMetrics())
})

onMounted(() => {
  loadTimeline()
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
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <div class="benchmark-body">
      <div class="benchmark-chat-pane">
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
              <MessageBubble :message="toBubbleMessage(entry.message)" show-timestamp />
            </div>

            <div
              v-else
              class="benchmark-row benchmark-transition-row"
              :class="{ 'benchmark-row-selected': isTransitionSelected(entry.transition) }"
              @click="selectTransition(entry.transition)"
            >
              <span class="benchmark-transition-arrow">→</span>
              <span class="benchmark-transition-badge">{{ entry.transition.new_state }}</span>
            </div>
          </template>
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
          :closable="false"
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
  flex-direction: column;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
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
