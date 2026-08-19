<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatTimeline from './chat/ChatTimeline.vue'
import SessionsPanel from './chat/SessionsPanel.vue'
import Inspector from './inspector/Inspector.vue'
import InspectorGraphTab from './inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from './inspector/InspectorSignalsTab.vue'
import InspectorMetricsTab from './inspector/InspectorMetricsTab.vue'
import InspectorPerformanceTab from './inspector/InspectorPerformanceTab.vue'
import ErrorBanner from './ErrorBanner.vue'
import {
  getMessages, getSessionSignals, getSessions, getProjectGraph, postImportSession, putMessageExpectedState,
  putMessageExpectedSignals, putSessionLabeled, deleteSessionAnnotations, deleteSession
} from '../api.js'
import { currentSessionId, sessions, sessionsLoading, loadSessions, refreshSessionsQuietly, selectSession } from '../chatStore.js'
import {
  buildTimeline, highlightedStateKeyFor, nearestMessageIdAtOrBefore, signalValuesFor
} from '../benchmarkTimeline.js'
import { useLeaveConfirmation } from '../composables/useLeaveConfirmation.js'

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
// Project-wide, fetched once (see onMounted below) — whether a live turn
// evaluates on the assistant's own reply (true) or the user's own
// message (false). An imported session (see ChatSession.source) has no
// real Tracking rows to consult at all, so annotatableSignalsRow below
// falls back to this same convention instead, to decide which side of
// an imported session's own messages is a legitimate mark point.
const autotrackingOnAiMessage = ref(false)

const inspectorRef = ref(null)
const inspectorWidth = ref(360)
const inspectorCollapsed = ref(false)
// This view's own tab set — Performance instead of Env (see Inspector.
// vue's own slot-based contract; EditProjectView.vue passes a different
// set for its own live chat). An imported session (see
// currentSessionIsImported below) has no real avance-computed metrics
// history of its own to show at all — Metrics/Performance both read off
// live Tracking rows an import never produces (see MetricService/
// BenchmarkCalculator) — so only States/Signals (annotation surfaces)
// make sense there. Inspector.vue's own tabs watcher already falls back
// to the first tab whenever the active one stops being valid, so
// switching sessions never needs to reset inspectorActiveTab by hand.
const inspectorTabs = computed(() => {
  const base = [
    { id: 'states', label: 'States' },
    { id: 'signals', label: 'Signals' }
  ]
  if (currentSessionIsImported.value) return base
  return [...base, { id: 'metrics', label: 'Metrics' }, { id: 'performance', label: 'Performance' }]
})
const inspectorActiveTab = ref('states')
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

// Toggled by SessionsPanel.vue's own collapse button, same as
// ChatWindow.vue's own panel — but a local, independent open/closed flag:
// the main page's own Sessions panel (see chatStore.js's
// sessionsPanelOpen) is a separate piece of UI, hidden behind this
// full-screen overlay while it's open, and shouldn't change just because
// this view's own panel did.
function toggleBenchmarkSessionsPanel() {
  benchmarkSessionsPanelOpen.value = !benchmarkSessionsPanelOpen.value
  if (benchmarkSessionsPanelOpen.value) loadSessions(true)
}

// This view's own Sessions panel is the one place that reviews imported
// transcripts (see ChatSession.source) alongside live ones (see
// SessionsPanel.vue's own allowImport) — every load/refresh below passes
// includeImported so an imported session doesn't disappear from the list
// again after the very next reload.
async function handleImportSession(file) {
  try {
    const { session_id } = await postImportSession(file)
    // The list must actually contain the new session before it can be
    // looked up in it — refresh first, select second, not the other way
    // around.
    await refreshSessionsQuietly(true)
    const imported = sessions.value.find((s) => s.id === session_id)
    if (imported) selectSession(imported)
  } catch {
    // already surfaced via apiFetch (see <ErrorBanner /> above)
  }
}

// Picking a session here uses the exact same shared mechanism as every
// other session picker in the app (see chatStore.js's own selectSession,
// used by SessionsPanel.vue's other callers) — currentSessionId is the
// one source of truth, and the watcher below reacts to it changing.
function onSelectSession(session) {
  if (!confirmLeaveIfNeeded()) return
  selectSession(session)
}

// Only an imported session is ever deletable here (see SessionsPanel.
// vue's own deleteImportedOnly) — a live/native one is the record of a
// real conversation, not this view's own to discard. Mirrors chatStore.
// js's own handleDeleteSession, just against this view's own session
// list (refreshSessionsQuietly(true) — see handleImportSession's own
// docstring on why includeImported matters here) rather than the main
// chat's.
const deletingSessionId = ref(null)
async function handleDeleteSession(session) {
  if (!window.confirm(`Delete this imported session (${session.title || session.end_state})? This cannot be undone.`)) return
  deletingSessionId.value = session.id
  try {
    await deleteSession(session.id)
    if (session.id === currentSessionId.value) currentSessionId.value = null
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingSessionId.value = null
  }
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
      getSessions(true)
    ])
    rawMessages.value = messageRows
    signalsLog.value = signalRows
    sessionStartState.value = allSessions.find((s) => s.id === sessionId)?.start_state ?? null
    // Both the core Metrics tab (project-wide, but "live" means "as of
    // now" — a stale point-in-time cutoff from the previous session's
    // own selection would otherwise linger) and the session-scoped
    // Performance tab need a fresh fetch for *this* session — neither
    // reactively recomputes on its own (see InspectorMetricsTab.vue/
    // InspectorPerformanceTab.vue's own refresh(active), each a no-op
    // unless its own tab is the one currently showing). Relying on the
    // `selected` reset above alone isn't enough: switching sessions
    // while nothing was ever selected leaves `selected` at null both
    // before and after, so that watcher never fires at all.
    await nextTick()
    inspectorRef.value?.refresh()
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

// Chronological, merged view of the session's messages and its state
// transitions — real ones, plus any evaluation point an expert annotated
// even though nothing actually changed there. See benchmarkTimeline.js
// for the actual logic (and its own regression tests) — every function
// there is pure, taking rawMessages/signalsLog/sessionStartState
// explicitly instead of closing over these refs.
const timeline = computed(() =>
  buildTimeline(rawMessages.value, signalsLog.value, sessionStartState.value, { imported: currentSessionIsImported.value })
)

// The point in time currently reflected by the Inspector — a message or a
// transition clicked in the timeline (see selectMessage/selectTransition).
// null until the first click, showing just the project's own definitions
// with nothing highlighted.
const selected = ref(null)

function selectMessage(message) {
  selected.value = { kind: 'message', message }
}

function selectTransition(transition) {
  selected.value = { kind: 'transition', transition }
}

// See benchmarkTimeline.js for the actual logic (and its own regression
// tests) behind highlightedStateKey/signalValues below — both exist
// specifically to avoid landing one point behind the current selection's
// own evaluation (see highlightedStateKeyFor/signalValuesFor's own
// docstrings).
const highlightedStateKey = computed(() =>
  highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value)
)

// Only a transition has "the action that produced it" to highlight.
// old_state === '' (the automaton's own init transition) is a real,
// clickable edge in the graph too now — a transparent pseudo-node's own
// outgoing edge (see InspectorGraphTab.vue's isInitEdge) — so this no
// longer excludes it: every transition selection highlights *some* edge.
const firedActionEdge = computed(() => {
  if (selected.value?.kind !== 'transition') return null
  const t = selected.value.transition
  return { stateKey: t.old_state, actionName: t.action }
})

const untilMessageId = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'message') return selected.value.message.id
  // A transition auto-tracking produced is linked straight back to the
  // message whose evaluation caused it (see db.py's Signals.message) — an
  // exact lookup, only falling back to the nearest-before heuristic for a
  // manual action's transition, which was never evaluated from any
  // message at all.
  return selected.value.transition.message_id ?? nearestMessageIdAtOrBefore(rawMessages.value, selected.value.transition.timestamp)
})

const signalValues = computed(() => signalValuesFor(selected.value, signalsLog.value, rawMessages.value))

// Whether the session currently being reviewed was imported (see
// ChatSession.source) rather than played live — the one case with no
// real Tracking rows at all to consult for annotatableSignalsRow below
// (see tracking.session_import's own module docstring).
const currentSessionIsImported = computed(() => {
  return sessions.value.find((s) => s.id === currentSessionId.value)?.source === 'imported'
})

// A message is a legitimate mark point for an imported session (which
// has no real Tracking row to prove it) only on whichever side a live
// turn would actually have evaluated on — assistant if
// autotrackingOnAiMessage, user otherwise (see TrackingService.
// _materialize_imported_session_row, the backend's own mirror of this
// same rule).
function isImportedAnnotationPoint(message) {
  return message.role === (autotrackingOnAiMessage.value ? 'assistant' : 'user')
}

// The Signals row backing the current selection's own evaluation, if
// any — the row itself for a clicked transition auto-tracking produced
// (see its own message_id), or (found by message_id) the row a clicked
// message's own evaluation produced. null when there's no evaluation to
// annotate against at all — a manual action's transition (message_id
// null: see project_service.apply_manual_action), or a message
// auto-tracking never evaluated anything after (see Signals.message's own
// docstring) — the Inspector's annotation controls only ever show for a
// non-null value here. An imported session never has a real row for any
// message (see currentSessionIsImported) — a virtual one (no id yet,
// materialized backend-side the first time an annotation is actually
// written, see TrackingService._materialize_imported_session_row) steps
// in for whichever message is a legitimate mark point on its own session.
const annotatableSignalsRow = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'transition') {
    return selected.value.transition.message_id != null ? selected.value.transition : null
  }
  const message = selected.value.message
  const row = signalsLog.value.find((s) => s.message_id === message.id)
  if (row) return row
  if (currentSessionIsImported.value && isImportedAnnotationPoint(message)) {
    return { id: null, message_id: message.id, old_state: null, new_state: null, expected_state: null, expected_values: null, values: null }
  }
  return null
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

// The automaton's own starting point (old_state === "" — see
// resolveTransitionRow/syntheticSessionStartEntry, real or synthetic
// alike) has no real signal evaluation behind it at all — nothing was
// ever computed there to have an opinion about. An expert can still
// disagree about *where the automaton starts* (the expected-state
// control above), just never about signal values that don't exist.
const annotatableExpectedSignals = computed(() => {
  return annotatableSignalsRow.value != null && annotatableSignalsRow.value.old_state !== ''
})

// A full reload (rather than patching signalsLog in place) is needed
// because an annotation write can now change *which* Signals row exists
// for a message, not just its fields: annotating a session's own start
// point materializes a brand-new row (see backend ChatService.
// _materialize_session_start_row — the synthetic entry above has no real
// row/id yet), and clearing the last annotation on that same kind of row
// deletes it again (see _finalize_annotation_write). Re-selects the
// current transition by message_id (its own row id may have just changed
// underneath it) so the Inspector doesn't keep showing a stale snapshot.
async function reloadSignalsLog() {
  if (!currentSessionId.value) return
  signalsLog.value = await getSessionSignals(currentSessionId.value)
  if (selected.value?.kind === 'transition') {
    const messageId = selected.value.transition.message_id
    const match = timeline.value.find((e) => e.kind === 'transition' && e.transition.message_id === messageId)
    selected.value = match ? { kind: 'transition', transition: match.transition } : null
  }
  // Every caller of this is an annotation write (see onUpdateExpectedState/
  // onUpdateExpectedSignals/onUnlabelAll) — the Sessions panel's own
  // has_annotations tag for this exact session (see SessionsPanel.vue) can
  // only just have flipped either way, and won't otherwise refresh until
  // the panel is toggled closed and reopened. Quiet: a full loadSessions()
  // would flash the panel to "Loading…" for something the user never
  // asked to reload.
  await refreshSessionsQuietly(true)
}

async function onUpdateExpectedState(value) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    await putMessageExpectedState(messageId, value)
    hasUnconfirmedChanges.value = true
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  }
}

async function onUpdateExpectedSignals(values) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    await putMessageExpectedSignals(messageId, values)
    hasUnconfirmedChanges.value = true
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
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
    hasUnconfirmedChanges.value = true
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  } finally {
    unlabelingAll.value = false
  }
}

// The current session's own persisted "reviewed" flag (see backend
// ChatSession.labeled) — read straight off the Sessions panel's own list
// (its has_annotations field, see chatStore.js's sessions/ChatService.
// _session_payload), the single source of truth for it now, not
// recomputed from signalsLog the way hasAnyAnnotations above still is
// for "Unlabel all" (a genuinely different question: "is there anything
// to clear" vs. "has an expert signed off on this session").
const currentSessionLabeled = computed(() => {
  return sessions.value.find((s) => s.id === currentSessionId.value)?.has_annotations ?? false
})

// A reminder, not a constraint (see this file's own composable) — set the
// instant an annotation is actually changed during this visit, reset the
// instant a new visit to a (possibly different) session begins. Nothing
// here ever blocks a change from being made; it only informs the leave
// prompt below.
const hasUnconfirmedChanges = ref(false)
watch(currentSessionId, () => { hasUnconfirmedChanges.value = false })

// Only a genuine reminder when there's both something unconfirmed *and*
// the session still isn't marked done — an expert who already pressed
// "Mark done" after their edits has already confirmed them, nothing left
// to remind about.
const shouldConfirm = computed(() => hasUnconfirmedChanges.value && !currentSessionLabeled.value)
const { confirmLeaveIfNeeded } = useLeaveConfirmation(
  shouldConfirm,
  'You changed annotations in this session, which is not marked done yet. Leave anyway?'
)

function handleClose() {
  if (!confirmLeaveIfNeeded()) return
  emit('close')
}

const markingDone = ref(false)

async function onToggleMarkDone() {
  if (!currentSessionId.value) return
  markingDone.value = true
  try {
    await putSessionLabeled(currentSessionId.value, !currentSessionLabeled.value)
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  } finally {
    markingDone.value = false
  }
}

// Metrics aren't reactive to props on their own (see
// InspectorMetricsTab.vue's own refresh(active) docstring) — every
// selection change needs an explicit nudge, same as EditProjectView.vue's
// turnCount watcher. No Env tab here (see this view's own tabs, below),
// so no matching nudge.
watch(selected, () => {
  nextTick(() => inspectorRef.value?.refresh())
})

onMounted(() => {
  loadTimeline()
  // The Sessions panel starts open (see benchmarkSessionsPanelOpen) —
  // toggleBenchmarkSessionsPanel only loads on a closed-to-open flip, so
  // the initial open needs its own load.
  loadSessions(true)
  getProjectGraph(props.projectName).then((graph) => {
    autotrackingOnAiMessage.value = graph.autotracking_on_ai_message
  }).catch(() => {
    // already surfaced via apiFetch
  })
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
      <h2>Label sessions — {{ projectName }}</h2>
      <div class="benchmark-header-actions">
        <button class="close-btn" @click="handleClose">Back</button>
      </div>
    </div>

    <ErrorBanner />

    <div class="benchmark-body">
      <div class="benchmark-chat-pane">
        <div class="sessions-panel-wrap">
          <div class="sessions-panel" :class="{ 'sessions-panel-collapsed': !benchmarkSessionsPanelOpen }" :style="benchmarkSessionsPanelOpen ? { width: sessionsPanelWidth + 'px' } : null">
            <SessionsPanel
              :sessions="sessions"
              :loading="sessionsLoading"
              :current-session-id="currentSessionId"
              :deleting-session-id="deletingSessionId"
              :allow-create="false"
              :allow-delete="true"
              :delete-imported-only="true"
              :allow-import="true"
              :collapsed="!benchmarkSessionsPanelOpen"
              @update:collapsed="toggleBenchmarkSessionsPanel"
              @select="onSelectSession"
              @import="handleImportSession"
              @delete="handleDeleteSession"
            />
          </div>
          <div v-if="benchmarkSessionsPanelOpen" class="split-divider" @mousedown="startSessionsDrag"></div>
        </div>

        <div class="benchmark-chat-content">
          <div class="benchmark-chat-toolbar">
            <span class="benchmark-chat-title">Chat</span>
            <div class="benchmark-chat-toolbar-actions">
              <button
                type="button"
                class="benchmark-unlabel-all-btn"
                :disabled="!hasAnyAnnotations || unlabelingAll"
                @click="onUnlabelAll"
              >
                {{ unlabelingAll ? 'Unlabelling…' : 'Unlabel all' }}
              </button>
              <button
                type="button"
                class="benchmark-mark-done-btn"
                :class="{ 'benchmark-mark-done-btn-active': currentSessionLabeled }"
                :disabled="!currentSessionId || markingDone"
                @click="onToggleMarkDone"
              >
                {{ currentSessionLabeled ? '✓ Done' : 'Mark done' }}
              </button>
            </div>
          </div>

          <p v-if="loading" class="benchmark-status">Loading…</p>
          <p v-else-if="!currentSessionId" class="benchmark-status">
            No session selected — pick one from the Sessions panel first.
          </p>
          <p v-else-if="!timeline.length" class="benchmark-status">This session has no messages yet.</p>

          <ChatTimeline
            v-else
            :timeline="timeline"
            :signals-log="signalsLog"
            :selected="selected"
            :imported="currentSessionIsImported"
            @select-message="selectMessage"
            @select-transition="selectTransition"
          />
        </div>
      </div>

      <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

      <div
        class="benchmark-inspector-panel"
        :class="{ 'benchmark-inspector-panel-collapsed': inspectorCollapsed }"
        :style="inspectorCollapsed ? null : { '--inspector-width': inspectorWidth + 'px' }"
      >
        <Inspector
          ref="inspectorRef"
          :tabs="inspectorTabs"
          v-model:active-tab="inspectorActiveTab"
          v-model:collapsed="inspectorCollapsed"
        >
          <template #tab-states="{ registerTab }">
            <InspectorGraphTab
              :ref="registerTab('states')"
              :project-name="projectName"
              :highlighted-state-key="highlightedStateKey"
              :fired-action-edge="firedActionEdge"
              :annotatable="annotatableSignalsRow != null"
              :expected-state="expectedState"
              :imported="currentSessionIsImported"
              @update-expected-state="onUpdateExpectedState"
            />
          </template>
          <template #tab-signals="{ registerTab }">
            <InspectorSignalsTab
              :ref="registerTab('signals')"
              :project-name="projectName"
              :signal-values="signalValues"
              :annotatable="annotatableExpectedSignals"
              :expected-values="expectedValues"
              :state-key="highlightedStateKey"
              :imported="currentSessionIsImported"
              @update-expected-signals="onUpdateExpectedSignals"
            />
          </template>
          <template #tab-metrics="{ registerTab }">
            <InspectorMetricsTab :ref="registerTab('metrics')" :until-message-id="untilMessageId" />
          </template>
          <template #tab-performance="{ registerTab }">
            <InspectorPerformanceTab :ref="registerTab('performance')" :benchmark-session-id="currentSessionId" />
          </template>
        </Inspector>
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

.sessions-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
  transition: width 0.15s ease;
}

/* Collapsed (see SessionsPanel.vue's own always-visible header toggle) —
   a slim strip, same pattern as ChatWindow.vue's own equivalent. */
.sessions-panel-collapsed {
  width: 2.4rem !important;
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

.benchmark-chat-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
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

.benchmark-mark-done-btn {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: white;
  color: #2e7d32;
  cursor: pointer;
  font-size: 0.78rem;
}

.benchmark-mark-done-btn:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.benchmark-mark-done-btn-active {
  background: #2e7d32;
  color: white;
}

.benchmark-mark-done-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.benchmark-status {
  margin: auto;
  color: #444;
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

/* Collapsed (see Inspector.vue's own always-visible header toggle) —
   without this, width stayed pinned to --inspector-width regardless (the
   bug: an empty docked panel that never actually gave its own space back
   to the timeline/sessions split next to it). Same slim-strip convention
   EditProjectView.vue's own .inspector-panel-collapsed uses. */
.benchmark-inspector-panel-collapsed {
  width: 2.4rem !important;
}
</style>
