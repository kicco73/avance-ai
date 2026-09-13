import { computed, nextTick, ref, watch } from 'vue'
import {
  getHistory, getSessionSignals, getSessions, putMessageExpectedState, putMessageExpectedSignals,
  putMessageComment, deleteSessionAnnotations,
} from './api.js'
import { buildTimeline, highlightedStateKeyFor, signalValuesFor } from '../../testTimeline.js'
import { refreshSessionsQuietly } from '../../chatStore.js'
import { confirmDialog } from '../../dialogStore.js'

export function useSessionAnnotation(projectId, currentSessionId, currentSessionIsImported, inspectorRef) {
  const loading = ref(true)
  const rawMessages = ref([])
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
        getHistory(sessionId),
        getSessionSignals(sessionId),
        getSessions(projectId, true)
      ])
      rawMessages.value = messageRows
      signalsLog.value = signalRows
      sessionStartState.value = allSessions.find((s) => s.id === sessionId)?.start_state ?? null
      await nextTick()
      inspectorRef.value?.refresh()
    } catch {
    } finally {
      loading.value = false
    }
  }

  watch(currentSessionId, loadTimeline)

  const timeline = computed(() =>
    buildTimeline(rawMessages.value, signalsLog.value, sessionStartState.value, { imported: currentSessionIsImported.value })
  )

  const selected = ref(null)

  function selectMessage(message) {
    selected.value = { kind: 'message', message }
  }

  function selectTransition(transition) {
    selected.value = { kind: 'transition', transition }
  }

  const highlightedStateKey = computed(() =>
    highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value)
  )

  const firedActionEdge = computed(() => {
    if (selected.value?.kind !== 'transition') return null
    const t = selected.value.transition
    return { stateKey: t.old_state, actionName: t.action }
  })

  const signalValues = computed(() => signalValuesFor(selected.value, signalsLog.value, rawMessages.value))

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

  const annotatableMessageId = computed(() => {
    if (!annotatableSignalsRow.value) return null
    return selected.value.kind === 'message' ? selected.value.message.id : annotatableSignalsRow.value.message_id
  })

  const expectedState = computed(() => annotatableSignalsRow.value?.expected_state ?? null)
  const expectedValues = computed(() => {
    const raw = annotatableSignalsRow.value?.expected_values
    return raw ? JSON.parse(raw) : {}
  })

  const annotatableExpectedSignals = computed(() => {
    return annotatableSignalsRow.value != null && annotatableSignalsRow.value.old_state !== ''
  })

  async function reloadSignalsLog() {
    if (!currentSessionId.value) return
    signalsLog.value = await getSessionSignals(currentSessionId.value)
    if (selected.value?.kind === 'transition') {
      const messageId = selected.value.transition.message_id
      const match = timeline.value.find((e) => e.kind === 'transition' && e.transition.message_id === messageId)
      selected.value = match ? { kind: 'transition', transition: match.transition } : null
    }
    await refreshSessionsQuietly(true, projectId)
  }

  async function onUpdateExpectedState(value) {
    const messageId = annotatableMessageId.value
    if (messageId == null) return
    try {
      await putMessageExpectedState(messageId, value)
      await reloadSignalsLog()
      inspectorRef.value?.refresh()
    } catch {
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
    }
  }

  async function onSaveComment(messageId, comment) {
    try {
      await putMessageComment(messageId, comment)
      await reloadSignalsLog()
    } catch {
    }
  }

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
