
export function valuesToSignalValues(raw) {
  if (raw == null) return {}
  const parsed = JSON.parse(raw)
  return Object.fromEntries(Object.entries(parsed).map(([name, value]) => [name, { value, error: null }]))
}

export function orderKey(timestamp, messageId) {
  return timestamp != null ? timestamp : `#${String(messageId).padStart(12, '0')}`
}

function rowEffectiveTimestamp(row, rawMessages) {
  if (row.message_id == null) return row.timestamp
  const linkedMessage = rawMessages.find((m) => m.id === row.message_id)
  return linkedMessage ? linkedMessage.timestamp : row.timestamp
}

export function signalValuesAsOf(signalsLog, rawMessages, cutoffKey) {
  let latest = null
  let latestKey = null
  for (const row of signalsLog) {
    if (row.values == null) continue
    const key = orderKey(rowEffectiveTimestamp(row, rawMessages), row.message_id)
    if (key > cutoffKey) continue
    if (latest == null || key >= latestKey) {
      latest = row
      latestKey = key
    }
  }
  return latest ? valuesToSignalValues(latest.values) : {}
}

export function actualStateAtOrBefore(signalsLog, sessionStartState, timestamp) {
  let result = sessionStartState
  for (const row of signalsLog) {
    if (row.timestamp > timestamp) break
    if (row.new_state != null && row.new_state !== row.old_state) result = row.new_state
  }
  return result
}

export function resolveTransitionRow(row, signalsLog, sessionStartState, { imported = false } = {}) {
  if (imported) return { ...row, old_state: null, new_state: row.expected_state }
  if (row.new_state != null && row.new_state !== row.old_state) return row
  const actualState = actualStateAtOrBefore(signalsLog, sessionStartState, row.timestamp)
  return { ...row, old_state: actualState, new_state: actualState }
}

export function transitionAnnotationStatus(transition, { imported = false } = {}) {
  if (transition.expected_state == null) return null
  if (imported) return 'labelled'
  return transition.expected_state === transition.new_state ? 'correct' : 'incorrect'
}

export function effectiveTimestamp(entry, rawMessages) {
  if (entry.kind === 'message') return entry.message.timestamp
  const messageId = entry.transition.message_id
  const linkedMessage = messageId != null ? rawMessages.find((m) => m.id === messageId) : null
  return linkedMessage ? linkedMessage.timestamp : entry.transition.timestamp
}

function entryOrderKey(entry) {
  const messageId = entry.kind === 'message' ? entry.message.id : entry.transition.message_id
  return orderKey(entry.timestamp, messageId)
}

export function syntheticSessionStartEntry(signalsLog, rawMessages, sessionStartState) {
  const hasOwnStartRow = signalsLog.some((s) => s.old_state === '')
  const firstMessage = rawMessages[0]
  if (hasOwnStartRow || !firstMessage || sessionStartState == null) return null
  return {
    kind: 'transition',
    timestamp: firstMessage.timestamp,
    transition: {
      id: null,
      old_state: '',
      action: '',
      new_state: sessionStartState,
      expected_state: null,
      expected_values: null,
      message_id: firstMessage.id
    },
    annotationStatus: null
  }
}

export function buildTimeline(rawMessages, signalsLog, sessionStartState, { includeSelfLoops = false, imported = false } = {}) {
  const messageEntries = rawMessages.map((m) => ({ kind: 'message', timestamp: m.timestamp, message: m }))
  const transitionEntries = signalsLog
    .filter((s) => {
      if (s.new_state == null) return s.expected_state != null
      const isSelfLoop = s.new_state === s.old_state
      return !isSelfLoop || includeSelfLoops || s.expected_state != null
    })
    .map((s) => {
      const transition = resolveTransitionRow(s, signalsLog, sessionStartState, { imported })
      const entry = { kind: 'transition', timestamp: s.timestamp, transition, annotationStatus: null }
      entry.timestamp = effectiveTimestamp(entry, rawMessages)
      entry.annotationStatus = transitionAnnotationStatus(transition, { imported })
      return entry
    })
  const synthetic = syntheticSessionStartEntry(signalsLog, rawMessages, sessionStartState)
  if (synthetic) transitionEntries.push(synthetic)
  return [...messageEntries, ...transitionEntries].sort((a, b) => {
    const ta = entryOrderKey(a)
    const tb = entryOrderKey(b)
    if (ta !== tb) return ta.localeCompare(tb)
    const aIsInit = a.kind === 'transition' && a.transition.old_state === ''
    const bIsInit = b.kind === 'transition' && b.transition.old_state === ''
    if (aIsInit !== bIsInit) return aIsInit ? -1 : 1
    return (a.kind === 'message' ? 0 : 1) - (b.kind === 'message' ? 0 : 1)
  })
}

export function stateAsOf(timeline, sessionStartState, cutoffKey) {
  let result = sessionStartState
  for (const entry of timeline) {
    if (entry.kind !== 'transition') continue
    if (entryOrderKey(entry) > cutoffKey) continue
    result = entry.transition.new_state
  }
  return result
}

export function nearestMessageIdAtOrBefore(rawMessages, timestamp) {
  let result = null
  for (const m of rawMessages) {
    if (m.timestamp > timestamp) break
    result = m.id
  }
  return result
}

export function messageHasAnnotatedSignals(message, signalsLog) {
  const row = signalsLog.find((s) => s.message_id === message.id)
  if (!row?.expected_values) return false
  try {
    const parsed = JSON.parse(row.expected_values)
    return parsed != null && Object.keys(parsed).length > 0
  } catch {
    return false
  }
}

export function commentForMessage(message, signalsLog) {
  const row = signalsLog.find((s) => s.message_id === message.id)
  return row?.comment || null
}

export function highlightedStateKeyFor(selected, timeline, sessionStartState) {
  if (!selected) return null
  if (selected.kind === 'transition') return selected.transition.new_state
  let result = sessionStartState
  for (const entry of timeline) {
    if (entry.kind === 'message' && entry.message.id === selected.message.id) break
    if (entry.kind === 'transition') result = entry.transition.new_state
  }
  return result
}

export function resultingStateKeyFor(selected, timeline, sessionStartState) {
  if (!selected) return null
  if (selected.kind === 'transition') return selected.transition.new_state
  const message = selected.message
  const ownTransition = timeline.find(
    (entry) => entry.kind === 'transition' && entry.transition.message_id === message.id
  )
  return ownTransition
    ? ownTransition.transition.new_state
    : stateAsOf(timeline, sessionStartState, orderKey(message.timestamp, message.id))
}

export function latestSignalValues(signalsLog) {
  for (let index = signalsLog.length - 1; index >= 0; index--) {
    if (signalsLog[index].values != null) return valuesToSignalValues(signalsLog[index].values)
  }
  return {}
}

export function signalValuesFor(selected, signalsLog, rawMessages = []) {
  if (!selected) return {}
  if (selected.kind === 'transition') {
    return valuesToSignalValues(selected.transition.values)
  }
  const linked = signalsLog.find((s) => s.message_id === selected.message.id)
  if (linked) return valuesToSignalValues(linked.values)
  return signalValuesAsOf(signalsLog, rawMessages, orderKey(selected.message.timestamp, selected.message.id))
}
