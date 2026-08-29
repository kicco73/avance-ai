import { computed, nextTick, ref, watch } from 'vue'
import {
  getMessages, getSessionSignals, getSessions, putMessageExpectedState, putMessageExpectedSignals,
  putMessageComment, deleteSessionAnnotations,
} from '../api.js'
import { buildTimeline, highlightedStateKeyFor, signalValuesFor } from '../testTimeline.js'
import { refreshSessionsQuietly } from '../chatStore.js'
import { confirmDialog } from '../dialogStore.js'

// The "Label sessions" view's own core: loads a session's messages/signals
// into a timeline, tracks which point is selected, and writes expert
// annotations (expected state/signals, comments) against it.
// `currentSessionId`/`currentSessionIsImported`/`inspectorRef` are owned by
// the caller (LabelProjectView.vue) — this composable only reads them.
export function useSessionAnnotation(projectName, currentSessionId, currentSessionIsImported, inspectorRef) {
  const loading = ref(true)
  // Raw backend message rows, kept as-is rather than chatStore.js's live
  // `messages` shape — this view reviews a fixed past session, not a live
  // conversation.
  const rawMessages = ref([])
  // The session's full Signals event log — timeline transitions,
  // point-in-time signal reconstructions, and annotations are all derived
  // from this alone, with no further backend round trips.
  const signalsLog = ref([])
  const sessionStartState = ref(null)

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
        getSessions(projectName, true)
      ])
      rawMessages.value = messageRows
      signalsLog.value = signalRows
      sessionStartState.value = allSessions.find((s) => s.id === sessionId)?.start_state ?? null
      // Some tabs (e.g. States/Signals) don't reactively recompute on
      // session change alone, so this refreshes whichever one's active —
      // the `selected` reset above isn't enough when `selected` was
      // already null.
      await nextTick()
      inspectorRef.value?.refresh()
    } catch {
      // already surfaced via apiFetch
    } finally {
      loading.value = false
    }
  }

  watch(currentSessionId, loadTimeline)

  // Chronological, merged view of the session's messages and state
  // transitions — real ones, plus any evaluation point an expert annotated
  // even though nothing actually changed there. See testTimeline.js.
  const timeline = computed(() =>
    buildTimeline(rawMessages.value, signalsLog.value, sessionStartState.value, { imported: currentSessionIsImported.value })
  )

  // The point in time currently reflected by the Inspector — a message or
  // transition clicked in the timeline. null until the first click.
  const selected = ref(null)

  function selectMessage(message) {
    selected.value = { kind: 'message', message }
  }

  function selectTransition(transition) {
    selected.value = { kind: 'transition', transition }
  }

  // See testTimeline.js — avoids landing one point behind the
  // current selection's own evaluation.
  const highlightedStateKey = computed(() =>
    highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value)
  )

  // Only a transition has "the action that produced it" to highlight.
  // old_state === '' (the automaton's init transition) is a real edge in
  // the graph too (see InspectorGraphTab.vue's isInitEdge).
  const firedActionEdge = computed(() => {
    if (selected.value?.kind !== 'transition') return null
    const t = selected.value.transition
    return { stateKey: t.old_state, actionName: t.action }
  })

  const signalValues = computed(() => signalValuesFor(selected.value, signalsLog.value, rawMessages.value))

  // The Signals row backing the current selection; null means no
  // evaluation exists to annotate against. An imported session never has a
  // real row, so a virtual placeholder stands in until the first write.
  const annotatableSignalsRow = computed(() => {
    if (!selected.value) return null
    if (selected.value.kind === 'transition') {
      return selected.value.transition.message_id != null ? selected.value.transition : null
    }
    const message = selected.value.message
    const row = signalsLog.value.find((s) => s.message_id === message.id)
    if (row) return row
    if (currentSessionIsImported.value) {
      return { id: null, message_id: message.id, old_state: null, new_state: null, expected_state: null, expected_values: null, values: null }
    }
    return null
  })

  // The annotation API is message-centric, so a transition selection
  // resolves back to whichever message its row says caused it.
  const annotatableMessageId = computed(() => {
    if (!annotatableSignalsRow.value) return null
    return selected.value.kind === 'message' ? selected.value.message.id : annotatableSignalsRow.value.message_id
  })

  const expectedState = computed(() => annotatableSignalsRow.value?.expected_state ?? null)
  const expectedValues = computed(() => {
    const raw = annotatableSignalsRow.value?.expected_values
    return raw ? JSON.parse(raw) : {}
  })

  // The automaton's starting point (old_state === "") has no real signal
  // evaluation behind it — an expert can disagree about where the
  // automaton starts, but never about signal values that don't exist.
  const annotatableExpectedSignals = computed(() => {
    return annotatableSignalsRow.value != null && annotatableSignalsRow.value.old_state !== ''
  })

  // A full reload is needed because an annotation write can change which
  // row exists for a message, not just its fields. Re-selects by
  // message_id since the row's own id may have just changed.
  async function reloadSignalsLog() {
    if (!currentSessionId.value) return
    signalsLog.value = await getSessionSignals(currentSessionId.value)
    if (selected.value?.kind === 'transition') {
      const messageId = selected.value.transition.message_id
      const match = timeline.value.find((e) => e.kind === 'transition' && e.transition.message_id === messageId)
      selected.value = match ? { kind: 'transition', transition: match.transition } : null
    }
    // The Sessions panel's has_annotations tag may have just flipped;
    // quiet, so it doesn't flash the panel to "Loading…" for a reload the
    // user never asked for.
    await refreshSessionsQuietly(true, projectName)
  }

  async function onUpdateExpectedState(value) {
    const messageId = annotatableMessageId.value
    if (messageId == null) return
    try {
      await putMessageExpectedState(messageId, value)
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
      await reloadSignalsLog()
      inspectorRef.value?.refresh()
    } catch {
      // already surfaced via apiFetch
    }
  }

  // Keyed directly off the clicked message's id rather than the current
  // Inspector selection — the comment icon sits on every message row.
  async function onSaveComment(messageId, comment) {
    try {
      await putMessageComment(messageId, comment)
      await reloadSignalsLog()
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
    const ok = await confirmDialog({
      title: 'Remove annotations',
      body: 'Remove every annotation in this session? This cannot be undone.',
      okLabel: 'Remove',
      danger: true
    })
    if (!ok) return
    unlabelingAll.value = true
    try {
      await deleteSessionAnnotations(currentSessionId.value)
      await reloadSignalsLog()
      inspectorRef.value?.refresh()
    } catch {
      // already surfaced via apiFetch
    } finally {
      unlabelingAll.value = false
    }
  }

  return {
    loading, rawMessages, signalsLog, sessionStartState, loadTimeline, timeline,
    selected, selectMessage, selectTransition, highlightedStateKey, firedActionEdge, signalValues,
    annotatableSignalsRow, annotatableMessageId, expectedState, expectedValues, annotatableExpectedSignals,
    reloadSignalsLog, onUpdateExpectedState, onUpdateExpectedSignals, onSaveComment,
    hasAnyAnnotations, unlabelingAll, onUnlabelAll,
  }
}
