// Pure, framework-agnostic timeline/signal logic for LabelProjectView.vue
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

// A comparable ordering key for anything with its own timestamp and a
// linked message id (a message itself, or a Signals/Tracking row linked
// to one via message_id) — the real ISO timestamp when there is one, or
// (an imported session — see session_import.py's own save_message(...,
// timestamp=None): every message, and every Tracking row materialized
// against one (see TrackingService._materialize_imported_session_row's
// own save_transition call, which never passes an explicit timestamp
// either — so that row instead gets stamped with *whenever an expert
// happened to annotate it*, unrelated to the message's own position in
// the transcript) has no real timestamp at all there) a zero-padded,
// lexicographically-sortable stand-in built from the linked message's
// own id instead. get_messages' own id order is already the correct
// chronological order for any session, native or imported (see db.py's
// own comment on why) — using it here is what keeps every "at or
// before" comparison below anchored to the message's actual position in
// the conversation, never to an annotation row's own unrelated creation
// time. A session's rows are either all real timestamps or all null,
// never mixed, so a fallback key here is never compared against a real
// timestamp string from the very same lookup.
export function orderKey(timestamp, messageId) {
  return timestamp != null ? timestamp : `#${String(messageId).padStart(12, '0')}`
}

// A Signals/Tracking row's own effective timestamp for "at or before"
// comparisons — same preference effectiveTimestamp below already applies
// to a timeline's own transition entries, and for the same two reasons:
// a live turn's own auto-tracking evaluation can complete fractionally
// before/after the message that caused it is actually saved, and (the
// case that matters here) an imported session's row is always linked to
// a message (see TrackingService._materialize_imported_session_row) but
// stamped with whenever an expert happened to annotate it, never the
// message's own position in the transcript at all — so the row's own
// raw `timestamp` must never be trusted directly, only ever its linked
// message's. Falls back to the row's own raw timestamp only when it
// isn't linked to any message at all (a manual action's snapshot).
function rowEffectiveTimestamp(row, rawMessages) {
  if (row.message_id == null) return row.timestamp
  const linkedMessage = rawMessages.find((m) => m.id === row.message_id)
  return linkedMessage ? linkedMessage.timestamp : row.timestamp
}

// The latest Signals row that actually carries values (a plain snapshot,
// or a transition that had signal_values — see db.py's Signals.values) at
// or before `cutoffKey` (see orderKey — a message's own order key, not a
// raw timestamp) — only a fallback for a message with no evaluation of
// its own (see signalValuesFor). Compares each row by its own *effective*
// timestamp (see rowEffectiveTimestamp), never its raw one — this is
// what keeps an imported session's own annotation-time-stamped rows
// correctly ordered by the message they're actually about, instead of by
// whenever an expert happened to click annotate.
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
export function resolveTransitionRow(row, signalsLog, sessionStartState, { imported = false } = {}) {
  // An imported session never has a real avance-computed new_state at all
  // (see TrackingService._materialize_imported_session_row's own
  // save_transition(None, None, None, ...) — old_state/action/new_state
  // are always null there): actualStateAtOrBefore would just keep
  // returning sessionStartState (itself null for an import — see
  // session_import.py's own create_chat_session(..., start_state=None))
  // for every row, so there's nothing genuine to resolve against. The
  // row's own annotated expected_state is the only real state anyone
  // ever attached here, so it stands in for new_state directly — see
  // transitionAnnotationStatus's own imported branch, which relies on
  // this to render the annotated state's name instead of a blank badge.
  if (imported) return { ...row, old_state: null, new_state: row.expected_state }
  if (row.new_state != null && row.new_state !== row.old_state) return row
  const actualState = actualStateAtOrBefore(signalsLog, sessionStartState, row.timestamp)
  return { ...row, old_state: actualState, new_state: actualState }
}

// Whether a transition's own expert-annotated expected_state (see
// Signals.expected_state — lives directly on the transition's own row
// now, no message lookup needed) agrees with what actually happened —
// null when unannotated (the timeline shows no verdict either way, same
// as the Inspector's own States tab). An imported session has no real
// avance-computed state to compare against at all (see
// resolveTransitionRow's own imported branch) — "correct"/"incorrect"
// would be meaningless there (an expert's own annotation compared
// against itself always "matches"), so this reports the neutral
// 'labelled' verdict instead, whenever something has actually been
// annotated.
export function transitionAnnotationStatus(transition, { imported = false } = {}) {
  if (transition.expected_state == null) return null
  if (imported) return 'labelled'
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

// A timeline entry's own order key — entry.timestamp is always already
// the *effective* one by the time this is ever called (see buildTimeline,
// which resolves it up front via effectiveTimestamp for every entry it
// produces, transitions included — never the transition's own raw
// timestamp), so this never needs rawMessages of its own to re-derive it.
function entryOrderKey(entry) {
  const messageId = entry.kind === 'message' ? entry.message.id : entry.transition.message_id
  return orderKey(entry.timestamp, messageId)
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
// An unannotated *plain snapshot* (old_state/new_state both null — no
// action fired at all, just an auto-tracking evaluation) still has
// nothing worth showing on its own, same exclusion db.
// get_last_transition_timestamp already applies for history-cutoff
// purposes — that one's never included, annotated or not.
// A fired *self-loop* (old_state === new_state, both real) is a
// different case: `includeSelfLoops` (EditProjectView.vue's own live
// chat, where the reviewer wants to see every action actually fire, not
// just the ones that moved somewhere) includes it unconditionally, same
// as an annotated one already was for LabelProjectView.vue's
// "Label sessions" review (unannotated ones stay excluded there, by
// omitting this flag — self-loops the model already didn't get flagged
// on aren't worth the clutter for that view's own purpose).
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
      // Resolved here, once, rather than left as the row's own raw
      // timestamp (see effectiveTimestamp's own docstring on why a
      // linked transition's raw timestamp is never trustworthy on its
      // own) — every later consumer of this entry (this function's own
      // sort below, but also stateAsOf/nearestMessageIdAtOrBefore et al,
      // called against the timeline this returns) then just reads
      // entry.timestamp directly, never needing rawMessages of its own
      // to re-derive the same thing a second time.
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
    // The same effective moment only happens when a transition is tied to
    // its own linked message. For every ordinary transition that message
    // is the *cause* (the user message auto-tracking evaluated), so it
    // reads first, explaining the transition right after it. The
    // automaton's own init transition (old_state === "", real or
    // synthetic — see resolveTransitionRow/syntheticSessionStartEntry)
    // is the one exception: its own linked message is the *opening*
    // bubble it produced by landing there, not a cause — so there the
    // transition must read first, right before the very bubble generated
    // for entering that state.
    const aIsInit = a.kind === 'transition' && a.transition.old_state === ''
    const bIsInit = b.kind === 'transition' && b.transition.old_state === ''
    if (aIsInit !== bIsInit) return aIsInit ? -1 : 1
    return (a.kind === 'message' ? 0 : 1) - (b.kind === 'message' ? 0 : 1)
  })
}

// The state active immediately after the latest transition at or before
// `cutoffKey` (see orderKey — a message's own order key, not a raw
// timestamp). Compares each transition by entryOrderKey — entry.timestamp
// is trusted directly, same as before this session's own fix, but a
// timeline built by buildTimeline now already carries the *effective*
// one for every transition (never its own raw timestamp) — that's what
// used to break this for an imported session: every Tracking row there
// gets stamped with whenever an expert happened to annotate it, never
// null like the messages themselves are, so comparing a transition's own
// raw timestamp against a null message cutoff silently never skipped
// anything and always landed on whichever row was annotated most
// recently in real time, regardless of that row's own position in the
// transcript. A hand-built timeline (e.g. in tests) that sets
// entry.timestamp directly still works exactly as it always did — this
// never re-derives it, only compares whatever's already there.
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

// Whether the Signals row a message's own evaluation produced (see
// Signals.message_id) has at least one expert-annotated expected signal
// value — drives MessageBubble.vue's "!" marker. Independent of a
// transition's own ✓/✕ indicator (see transitionAnnotationStatus), which
// is about expected_state, not expected_values. Pure over any signalsLog
// (benchmark or live), so ChatTimeline.vue can call it regardless of which
// view supplied the log.
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

// A message's own expert-left free-text comment (see Signals.comment),
// or null if none — drives MessageBubble.vue's comment icon (filled vs
// outline) and pre-fills its popover. Independent of
// messageHasAnnotatedSignals above (a different Tracking field
// entirely) and, unlike it, never gated on the row actually being an
// "evaluation point" — see TrackingService.set_message_comment.
export function commentForMessage(message, signalsLog) {
  const row = signalsLog.find((s) => s.message_id === message.id)
  return row?.comment || null
}

// The state key the Inspector (every tab alike — the Graph tab's own
// highlight and the Signals tab's own "relevant" filter both read this
// exact same value, deliberately: they must never disagree) should
// treat as current for the selection — a message or a transition
// clicked in the timeline. A message ALWAYS resolves to whatever state
// was genuinely active when it was written/received, even when its own
// evaluation went on to cause a transition: buildTimeline's own sort
// always places a message before its own linked transition (they tie on
// effective timestamp, and the tie-break reads message-first — see
// buildTimeline's own comment on why, and effectiveTimestamp's), so
// visually the message still sits in the *previous* block, with the
// transition marker as the actual boundary where the new state begins —
// showing the new state already at the message itself would highlight
// something that, from that message's own point of view, hadn't
// happened yet. Only a transition (the marker itself, or anything
// timestamped after it) ever resolves to new_state. For the different
// (and unrelated) question of "what did this message's own turn
// ultimately leave the conversation in" — e.g. RestartFromHereButton's
// own validity check — see resultingStateKeyFor instead, which keeps
// the old preference this function itself used to have.
export function highlightedStateKeyFor(selected, timeline, sessionStartState) {
  if (!selected) return null
  if (selected.kind === 'transition') return selected.transition.new_state
  return stateAsOf(timeline, sessionStartState, orderKey(selected.message.timestamp, selected.message.id))
}

// "What state did this message's own turn ultimately leave the
// conversation in" — unlike highlightedStateKeyFor, this DOES prefer a
// transition *directly linked* to this exact message (see Signals.
// message) over the raw-timestamp fallback below, since here the
// question being asked is different: not "what was true when this
// message arrived" but "once this message was fully processed,
// including whatever it caused, where did things end up" — e.g.
// RestartFromHereButton's own isStateGone check, which needs to know
// the *resulting* state to tell whether it still exists in the current
// project, not the state the message merely arrived into. That
// transition's own row is timestamped fractionally *after* the message
// that caused it (auto-tracking's own evaluation runs once the message
// is already saved — see effectiveTimestamp's own docstring), so
// stateAsOf(message.timestamp) alone would always land one step behind.
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
// `rawMessages` is only ever consulted by the last-resort fallback below
// (see signalValuesAsOf/rowEffectiveTimestamp) — every other branch
// already has everything it needs on `selected`/`signalsLog` alone.
export function signalValuesFor(selected, signalsLog, rawMessages = []) {
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
  return signalValuesAsOf(signalsLog, rawMessages, orderKey(selected.message.timestamp, selected.message.id))
}
