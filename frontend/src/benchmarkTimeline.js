// Pure, framework-agnostic timeline/signal logic for BenchmarkProjectView.vue
// — extracted so the bugs each function exists to prevent (see their own
// docstrings; found and fixed by hand this session) have a real,
// repo-resident regression test instead of only ever having been
// eyeballed. Every function takes its data explicitly (signalsLog,
// rawMessages, sessionStartState, ...) rather than closing over Vue refs,
// so the component just wraps these with `.value` at each call site.

// Reshapes a Signals row's own raw `values` JSON (or null) into
// Inspector's own { [name]: { value, error } } signalValues prop shape
// (see AutoTracker.run — a persisted snapshot is always a plain
// {name: value} dict, never {value, error}, so error is always null
// here). Shared by every "what were the signals right here" lookup below.
export function valuesToSignalValues(raw) {
  if (raw == null) return {}
  const parsed = JSON.parse(raw)
  return Object.fromEntries(Object.entries(parsed).map(([name, value]) => [name, { value, error: null }]))
}

// The latest Signals row that actually carries values (a plain snapshot,
// or a transition that had signal_values — see db.py's Signals.values) at
// or before `timestamp` — only a fallback for a message with no
// evaluation of its own (see signalValuesFor); a row's own evaluation can
// be timestamped fractionally *after* the message that caused it (see
// effectiveTimestamp's own docstring), so this would otherwise always
// land one point behind for a message that does have one.
export function signalValuesAsOf(signalsLog, timestamp) {
  let latest = null
  for (const row of signalsLog) {
    if (row.timestamp > timestamp || row.values == null) continue
    if (latest == null || row.timestamp >= latest.timestamp) latest = row
  }
  return latest ? valuesToSignalValues(latest.values) : {}
}

// The state genuinely in effect at or before `timestamp`, per only the
// *real* transitions in signalsLog (own new_state !== own old_state) —
// used to resolve what a no-real-change row (see resolveTransitionRow)
// should display as its own old/new state. signalsLog is already
// chronological (see db.get_signals), so a single forward scan is enough.
export function actualStateAtOrBefore(signalsLog, sessionStartState, timestamp) {
  let result = sessionStartState
  for (const row of signalsLog) {
    if (row.timestamp > timestamp) break
    if (row.new_state != null && row.new_state !== row.old_state) result = row.new_state
  }
  return result
}

// An expert can annotate expected_state on *any* evaluation point, not
// just one that happened to fire a real transition — e.g. "the state
// should have changed here, but didn't" is exactly the kind of miss
// state_accuracy exists to catch (see metrics_framework/benchmark_metrics).
// A row with no real transition of its own (a plain auto-tracking
// snapshot, old_state/new_state both null, or a fired self-loop,
// old_state === new_state) has nothing of its own to show as "old_state
// -> new_state" though, so both are filled in with whatever state was
// actually in effect at that point — same value on both sides, same as
// a self-loop reads today, so transitionAnnotationStatus's own
// expected_state/new_state comparison still means the right thing.
export function resolveTransitionRow(row, signalsLog, sessionStartState) {
  if (row.new_state != null && row.new_state !== row.old_state) return row
  const actualState = actualStateAtOrBefore(signalsLog, sessionStartState, row.timestamp)
  return { ...row, old_state: actualState, new_state: actualState }
}

// Whether a transition's own expert-annotated expected_state (see
// Signals.expected_state — lives directly on the transition's own row
// now, no message lookup needed) agrees with what actually happened —
// null when unannotated (the timeline shows no verdict either way, same
// as the Inspector's own States tab).
export function transitionAnnotationStatus(transition) {
  if (transition.expected_state == null) return null
  return transition.expected_state === transition.new_state ? 'correct' : 'incorrect'
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
export function effectiveTimestamp(entry, rawMessages) {
  if (entry.kind === 'message') return entry.message.timestamp
  const messageId = entry.transition.message_id
  const linkedMessage = messageId != null ? rawMessages.find((m) => m.id === messageId) : null
  return linkedMessage ? linkedMessage.timestamp : entry.transition.timestamp
}

// Only the literal first session ever opened for a project gets a real
// "" -> start_state Signals row (see backend ChatService.open_if_needed) —
// every other session genuinely has no such row, so there's nothing here
// to build a real transitionEntries row from. A synthetic one is added
// instead purely so the reviewer can see (and annotate) where the
// conversation began — annotating it materializes the real row backend-
// side (see ChatService._materialize_session_start_row), which replaces
// this synthetic entry on the next reload; clearing that annotation
// deletes the row again (see _finalize_annotation_write), bringing this
// synthetic entry right back.
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
// transitions — real ones, plus any evaluation point an expert annotated
// even though nothing actually changed there (see resolveTransitionRow).
// An unannotated self-loop/plain-snapshot still has nothing worth
// showing on its own, same exclusion db.get_last_transition_timestamp
// already applies for history-cutoff purposes.
export function buildTimeline(rawMessages, signalsLog, sessionStartState) {
  const messageEntries = rawMessages.map((m) => ({ kind: 'message', timestamp: m.timestamp, message: m }))
  const transitionEntries = signalsLog
    .filter((s) => (s.new_state != null && s.new_state !== s.old_state) || s.expected_state != null)
    .map((s) => {
      const transition = resolveTransitionRow(s, signalsLog, sessionStartState)
      return {
        kind: 'transition',
        timestamp: s.timestamp,
        transition,
        annotationStatus: transitionAnnotationStatus(transition)
      }
    })
  const synthetic = syntheticSessionStartEntry(signalsLog, rawMessages, sessionStartState)
  if (synthetic) transitionEntries.push(synthetic)
  return [...messageEntries, ...transitionEntries].sort((a, b) => {
    const ta = effectiveTimestamp(a, rawMessages)
    const tb = effectiveTimestamp(b, rawMessages)
    if (ta !== tb) return ta.localeCompare(tb)
    // The same effective moment only happens when a transition is tied to
    // this exact message (see effectiveTimestamp) — the message it's
    // explaining always reads first.
    return (a.kind === 'message' ? 0 : 1) - (b.kind === 'message' ? 0 : 1)
  })
}

// The state active immediately after the latest transition at or before
// `timestamp`, or the session's own starting state if none has fired yet.
export function stateAsOf(timeline, sessionStartState, timestamp) {
  let result = sessionStartState
  for (const entry of timeline) {
    if (entry.kind !== 'transition' || entry.timestamp > timestamp) continue
    result = entry.transition.new_state
  }
  return result
}

// A transition has no message_id of its own, but point-in-time metrics
// can only be pinned to one (see api.js's getMetrics/chat_service.py's
// get_metrics — "Message.timestamp", per the message_id it resolves
// internally) — so a transition's own metrics use whichever message most
// recently preceded it.
export function nearestMessageIdAtOrBefore(rawMessages, timestamp) {
  let result = null
  for (const m of rawMessages) {
    if (m.timestamp > timestamp) break
    result = m.id
  }
  return result
}

// The state key the Inspector should highlight for the current
// selection — a message or a transition clicked in the timeline.
export function highlightedStateKeyFor(selected, timeline, sessionStartState) {
  if (!selected) return null
  if (selected.kind === 'transition') return selected.transition.new_state
  // Prefer the state a transition *directly linked* to this exact
  // message produced (see Signals.message) over the raw-timestamp
  // fallback below: that transition's own row is timestamped fractionally
  // *after* the message that caused it (auto-tracking's own evaluation
  // runs once the message is already saved — see effectiveTimestamp's own
  // docstring), so stateAsOf(message.timestamp) would otherwise always
  // land one step behind, showing the state as it was *before* this
  // message instead of what it became because of it.
  const message = selected.message
  const ownTransition = timeline.find(
    (entry) => entry.kind === 'transition' && entry.transition.message_id === message.id
  )
  return ownTransition ? ownTransition.transition.new_state : stateAsOf(timeline, sessionStartState, message.timestamp)
}

// The signal values the Inspector should show for the current selection.
export function signalValuesFor(selected, signalsLog) {
  if (!selected) return {}
  if (selected.kind === 'transition') {
    // Whatever this row itself observed — never a timestamp lookup,
    // which risks landing on the *previous* row instead (see
    // signalValuesAsOf's own docstring). A row with nothing real behind
    // it (the synthetic session-start entry, a manual action, an
    // unfired self-loop) correctly has no values of its own, so this
    // reads as n/a rather than falling back to "whatever came before".
    return valuesToSignalValues(selected.transition.values)
  }
  const linked = signalsLog.find((s) => s.message_id === selected.message.id)
  if (linked) return valuesToSignalValues(linked.values)
  // This message was never itself an evaluation point (e.g. an assistant
  // reply auto-tracking didn't run for) — the closest thing still true
  // is whatever the latest real evaluation showed strictly before it.
  return signalValuesAsOf(signalsLog, selected.message.timestamp)
}
