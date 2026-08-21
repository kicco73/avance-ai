// Pure, framework-agnostic timeline/signal logic for LabelProjectView.vue.
// Every function takes its data explicitly rather than closing over Vue
// refs, so the component just wraps these with `.value` at each call site.

// Reshapes a Signals row's raw `values` JSON (or null) into the
// Inspector's { [name]: { value, error } } signalValues shape — a
// persisted snapshot has no error info, so error is always null here.
export function valuesToSignalValues(raw) {
  if (raw == null) return {}
  const parsed = JSON.parse(raw)
  return Object.fromEntries(Object.entries(parsed).map(([name, value]) => [name, { value, error: null }]))
}

// A comparable ordering key for anything with a timestamp and a linked
// message id. Imported sessions have no real timestamps, so this falls
// back to a zero-padded, sortable key built from the message id instead.
export function orderKey(timestamp, messageId) {
  return timestamp != null ? timestamp : `#${String(messageId).padStart(12, '0')}`
}

// A Signals/Tracking row's effective timestamp — a row linked to a
// message uses that message's own timestamp rather than its own raw one,
// since an imported row is stamped with annotation time, not position.
function rowEffectiveTimestamp(row, rawMessages) {
  if (row.message_id == null) return row.timestamp
  const linkedMessage = rawMessages.find((m) => m.id === row.message_id)
  return linkedMessage ? linkedMessage.timestamp : row.timestamp
}

// The latest Signals row with values at or before `cutoffKey` — a
// fallback for a message with no evaluation of its own (see
// signalValuesFor). Compares by each row's effective timestamp, not its raw one.
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

// The state genuinely in effect at or before `timestamp`, per only the
// real transitions in signalsLog (new_state !== old_state) — used to
// resolve what a no-real-change row should display as old/new state.
export function actualStateAtOrBefore(signalsLog, sessionStartState, timestamp) {
  let result = sessionStartState
  for (const row of signalsLog) {
    if (row.timestamp > timestamp) break
    if (row.new_state != null && row.new_state !== row.old_state) result = row.new_state
  }
  return result
}

// An expert can annotate expected_state on any evaluation point, not
// just one that fired a real transition. A row with no real transition
// has nothing to show as old/new state, so both get whatever was actually in effect.
export function resolveTransitionRow(row, signalsLog, sessionStartState, { imported = false } = {}) {
  // An imported session has no real computed new_state at all, so there's
  // nothing genuine to resolve against — the row's own annotated
  // expected_state stands in for new_state directly instead.
  if (imported) return { ...row, old_state: null, new_state: row.expected_state }
  if (row.new_state != null && row.new_state !== row.old_state) return row
  const actualState = actualStateAtOrBefore(signalsLog, sessionStartState, row.timestamp)
  return { ...row, old_state: actualState, new_state: actualState }
}

// Whether a transition's expert-annotated expected_state agrees with
// what actually happened — null when unannotated. An imported session
// has no real computed state to compare against, so this reports 'labelled' instead.
export function transitionAnnotationStatus(transition, { imported = false } = {}) {
  if (transition.expected_state == null) return null
  if (imported) return 'labelled'
  return transition.expected_state === transition.new_state ? 'correct' : 'incorrect'
}

// A transition linked to a message is positioned as if it happened when
// that message did: auto-tracking evaluates before the message is saved,
// so a transition's row can be timestamped earlier than its cause.
export function effectiveTimestamp(entry, rawMessages) {
  if (entry.kind === 'message') return entry.message.timestamp
  const messageId = entry.transition.message_id
  const linkedMessage = messageId != null ? rawMessages.find((m) => m.id === messageId) : null
  return linkedMessage ? linkedMessage.timestamp : entry.transition.timestamp
}

// A timeline entry's order key — entry.timestamp is already the
// effective one by the time this runs (buildTimeline resolves it via
// effectiveTimestamp up front), so this never needs rawMessages.
function entryOrderKey(entry) {
  const messageId = entry.kind === 'message' ? entry.message.id : entry.transition.message_id
  return orderKey(entry.timestamp, messageId)
}

// Only the very first session ever opened for a project gets a real
// "" -> start_state Signals row. A synthetic one is added instead so
// the reviewer can see (and annotate) where the conversation began.
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

// Chronological, merged view of the session's messages and its state
// transitions — real ones, plus any evaluation point an expert annotated.
// A fired self-loop is included only when `includeSelfLoops` is set.
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
      // Resolved here, once, so every later consumer just reads
      // entry.timestamp directly instead of re-deriving it via rawMessages.
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
    // A tied moment only happens when a transition is linked to a
    // message. Normally that message caused the transition, so it reads
    // first. The init transition is the exception: its linked message is
    // the opening bubble it produced, so the transition reads first there.
    const aIsInit = a.kind === 'transition' && a.transition.old_state === ''
    const bIsInit = b.kind === 'transition' && b.transition.old_state === ''
    if (aIsInit !== bIsInit) return aIsInit ? -1 : 1
    return (a.kind === 'message' ? 0 : 1) - (b.kind === 'message' ? 0 : 1)
  })
}

// The state active immediately after the latest transition at or before
// `cutoffKey`. Compares each transition by entryOrderKey, trusting
// entry.timestamp directly — buildTimeline already resolves it.
export function stateAsOf(timeline, sessionStartState, cutoffKey) {
  let result = sessionStartState
  for (const entry of timeline) {
    if (entry.kind !== 'transition') continue
    if (entryOrderKey(entry) > cutoffKey) continue
    result = entry.transition.new_state
  }
  return result
}

// A transition has no message_id of its own, but point-in-time metrics
// (see getMetrics in api.js) can only be pinned to one — so a
// transition's own metrics use whichever message most recently preceded it.
export function nearestMessageIdAtOrBefore(rawMessages, timestamp) {
  let result = null
  for (const m of rawMessages) {
    if (m.timestamp > timestamp) break
    result = m.id
  }
  return result
}

// Whether the Signals row a message's evaluation produced has at least
// one expert-annotated expected signal value — drives MessageBubble's
// "!" marker. Independent of transitionAnnotationStatus (expected_state).
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

// A message's expert-left free-text comment, or null if none — drives
// MessageBubble's comment icon and pre-fills its popover. Unlike
// messageHasAnnotatedSignals, never gated on being an "evaluation point".
export function commentForMessage(message, signalsLog) {
  const row = signalsLog.find((s) => s.message_id === message.id)
  return row?.comment || null
}

// The state key the Inspector treats as current for the selection. A
// message resolves to whatever state was active when it arrived, even
// if its own evaluation caused a transition (see resultingStateKeyFor).
// Walks the timeline directly rather than going through stateAsOf's key
// comparison: buildTimeline's own tie-break already sorts a message
// before the transition it caused, so stopping at the message avoids
// that transition (and anything after it) without needing to single out
// the tied entry by key.
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

// "What state did this turn ultimately leave the conversation in" —
// unlike highlightedStateKeyFor, prefers a transition directly linked to
// this message, e.g. for RestartFromHereButton's isStateGone check.
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

// The signal values the Inspector should show for the current selection.
// `rawMessages` is only consulted by the last-resort fallback below —
// every other branch already has what it needs on `selected`/`signalsLog`.
export function signalValuesFor(selected, signalsLog, rawMessages = []) {
  if (!selected) return {}
  if (selected.kind === 'transition') {
    // Whatever this row itself observed — never a timestamp lookup. A row
    // with nothing real behind it correctly has no values of its own.
    return valuesToSignalValues(selected.transition.values)
  }
  const linked = signalsLog.find((s) => s.message_id === selected.message.id)
  if (linked) return valuesToSignalValues(linked.values)
  // This message was never itself an evaluation point — fall back to
  // whatever the latest real evaluation showed strictly before it.
  return signalValuesAsOf(signalsLog, rawMessages, orderKey(selected.message.timestamp, selected.message.id))
}
