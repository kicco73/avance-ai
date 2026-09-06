import { computed, nextTick, ref } from 'vue'
import { getSessions, getSessionSignals, getSignals } from '../api.js'
import { buildTimeline, highlightedStateKeyFor, nearestMessageIdAtOrBefore, resultingStateKeyFor, signalValuesFor } from '../testTimeline.js'
import { testStore } from '../testChatStore.js'

// The "Run" tab's live conversation as a clickable message+transition
// timeline, plus the point in time the Inspector reflects (`selected`:
// null follows the live conversation, a value pins it to a bubble/edge).
export function useLiveRunTimeline(projectId, mode, validStateKeys) {
  const { state: runState, messages, currentSessionId, draft, handleSend, handleTruncateFrom } = testStore

  const signalValueByName = ref({})
  const signalsLog = ref([])
  const sessionStartState = ref(null)
  const selected = ref(null)
  const runChatRef = ref(null)

  // The in-flight assistant placeholder has no messageId yet: `key` carries
  // the store's own stable local id so ChatTimeline can key on it.
  const rawLiveMessages = computed(() =>
    messages.value.map((m) => ({ ...m, id: m.messageId ?? null, key: m.id, audio_text: m.audioText }))
  )

  const timeline = computed(() =>
    buildTimeline(rawLiveMessages.value, signalsLog.value, sessionStartState.value, { includeSelfLoops: true })
  )

  async function refreshSignalsLog() {
    if (!currentSessionId.value) {
      signalsLog.value = []
      return
    }
    try {
      signalsLog.value = await getSessionSignals(currentSessionId.value)
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function refreshSessionStartState() {
    if (!currentSessionId.value) {
      sessionStartState.value = null
      return
    }
    try {
      const allSessions = await getSessions(projectId)
      sessionStartState.value = allSessions.find((s) => s.id === currentSessionId.value)?.start_state ?? null
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function refreshSignalValues() {
    try {
      const nextValues = await getSignals()
      signalValueByName.value = Object.fromEntries(nextValues.map((s) => [s.name, { value: s.value, error: s.error }]))
    } catch {
      // already surfaced via apiFetch
    }
  }

  function isStateGone(message) {
    const stateKey = resultingStateKeyFor({ kind: 'message', message }, timeline.value, sessionStartState.value)
    return stateKey != null && !validStateKeys.value.has(stateKey)
  }

  function selectMessage(message) {
    selected.value =
      selected.value?.kind === 'message' && selected.value.message.id === message.id ? null : { kind: 'message', message }
  }

  function selectTransition(transition) {
    selected.value =
      selected.value?.kind === 'transition' && selected.value.transition.id === transition.id
        ? null
        : { kind: 'transition', transition }
  }

  const highlightedStateKey = computed(() => {
    if (mode.value !== 'run') return null
    return selected.value
      ? highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value)
      : (runState.value?.key ?? null)
  })

  const firedActionEdge = computed(() => {
    if (selected.value?.kind !== 'transition') return null
    const t = selected.value.transition
    return { stateKey: t.old_state, actionName: t.action }
  })

  const untilMessageId = computed(() => {
    if (!selected.value) return null
    if (selected.value.kind === 'message') return selected.value.message.id
    return (
      selected.value.transition.message_id ??
      nearestMessageIdAtOrBefore(rawLiveMessages.value, selected.value.transition.timestamp)
    )
  })

  const latestMessageId = computed(() => {
    const msgs = rawLiveMessages.value
    return msgs.length ? msgs[msgs.length - 1].id : null
  })
  const envEditable = computed(() =>
    !selected.value || (selected.value.kind === 'message' && selected.value.message.id === latestMessageId.value)
  )

  const effectiveSignalValues = computed(() =>
    selected.value ? signalValuesFor(selected.value, signalsLog.value, rawLiveMessages.value) : signalValueByName.value
  )

  async function restartAndPrefill(message) {
    await handleTruncateFrom(message.timestamp)
    selected.value = null
    draft.value = message.content
    await nextTick()
    runChatRef.value?.focus()
  }

  async function restartAndResend(message) {
    await handleTruncateFrom(message.timestamp)
    selected.value = null
    await handleSend(message.content)
  }

  return {
    signalsLog, sessionStartState, selected, runChatRef, timeline,
    refreshSignalsLog, refreshSessionStartState, refreshSignalValues, isStateGone,
    selectMessage, selectTransition, highlightedStateKey, firedActionEdge, untilMessageId, envEditable,
    effectiveSignalValues, restartAndPrefill, restartAndResend,
  }
}
