import { describe, expect, it } from 'vitest'
import {
  actualStateAtOrBefore,
  buildTimeline,
  commentForMessage,
  effectiveTimestamp,
  highlightedStateKeyFor,
  nearestMessageIdAtOrBefore,
  resolveTransitionRow,
  resultingStateKeyFor,
  signalValuesAsOf,
  signalValuesFor,
  stateAsOf,
  syntheticSessionStartEntry,
  transitionAnnotationStatus,
  valuesToSignalValues
} from '../src/testTimeline.js'

function message(id, timestamp, role = 'user') {
  return { id, role, content: 'hi', timestamp }
}

function transitionRow(id, { timestamp, oldState = null, newState = null, values = null, messageId = null, expectedState = null }) {
  return {
    id,
    timestamp,
    old_state: oldState,
    new_state: newState,
    action: newState ? 'advance' : null,
    values,
    expected_state: expectedState,
    expected_values: null,
    message_id: messageId
  }
}

function kinds(timeline) {
  return timeline.map((e) => (e.kind === 'message' ? `m${e.message.id}` : 't'))
}

function transitionIds(timeline) {
  return timeline.filter((e) => e.kind === 'transition').map((e) => e.transition.id)
}

describe('commentForMessage', () => {
  it('returns the linked row\'s comment, or null when there is no linked row, no comment, or a blank one', () => {
    const m = message(1, 't1')
    const withComment = (comment) => ({ ...transitionRow(1, { timestamp: 't1', messageId: 1 }), comment })

    expect(commentForMessage(m, [])).toBeNull()
    expect(commentForMessage(m, [withComment(null)])).toBeNull()
    expect(commentForMessage(m, [withComment('')])).toBeNull()
    expect(commentForMessage(m, [withComment('Double-checked this — looks right.')])).toBe('Double-checked this — looks right.')
    // Never matches a row linked to a different message.
    expect(commentForMessage(message(2, 't1'), [withComment('not this message')])).toBeNull()
  })
})

describe('valuesToSignalValues', () => {
  it('reshapes a plain {name: value} JSON string into {name: {value, error}}, empty for null/undefined', () => {
    expect(valuesToSignalValues(null)).toEqual({})
    expect(valuesToSignalValues(undefined)).toEqual({})
    expect(valuesToSignalValues(JSON.stringify({ foo: 42 }))).toEqual({ foo: { value: 42, error: null } })
  })
})

describe('signalValuesAsOf', () => {
  // Regression test: auto-tracking's own evaluation for a user message is
  // always timestamped *after* that message is saved (see backend
  // ChatService._process_turn_locked) — a naive "row.timestamp <=
  // given timestamp" scan against the *message's own* timestamp would
  // therefore always miss that row and fall back to the previous one.
  // signalValuesAsOf itself is only ever meant to be called with a row's
  // own timestamp (see signalValuesFor), never a message's directly.
  it('picks the latest row at or before the given timestamp, ignoring valueless rows and anything later', () => {
    const log = [
      transitionRow(1, { timestamp: '2026-01-01T10:00:00', values: JSON.stringify({ risk: 0 }) }),
      transitionRow(2, { timestamp: '2026-01-01T10:00:05', values: null }),
      transitionRow(3, { timestamp: '2026-01-01T10:00:10', values: JSON.stringify({ risk: 100 }) })
    ]

    expect(signalValuesAsOf(log, [], '2026-01-01T10:00:07')).toEqual({ risk: { value: 0, error: null } })
    expect(signalValuesAsOf(log, [], '2026-01-01T10:00:10')).toEqual({ risk: { value: 100, error: null } })
    expect(signalValuesAsOf(log, [], '2026-01-01T09:00:00')).toEqual({})
  })
})

describe('signalValuesFor — the off-by-one bug this session found and fixed', () => {
  // Reproduces the exact scenario reported: a risky user message (id=3)
  // whose own evaluation (Signals row id=2, message_id=3) is timestamped
  // *after* the message itself, because auto-tracking only ever runs
  // once its own triggering message is already saved. Selecting that
  // message must show *its own* evaluation's values (100), never the
  // previous point's (0).
  const log = [
    transitionRow(1, { timestamp: '2026-01-01T10:00:00.100', values: JSON.stringify({ detectRisk: 0 }), messageId: 1 }),
    transitionRow(2, {
      timestamp: '2026-01-01T10:00:02.100', // after message 3's own timestamp
      oldState: 'action',
      newState: 'crisis',
      values: JSON.stringify({ detectRisk: 100 }),
      messageId: 3
    })
  ]
  const RISK_100 = { detectRisk: { value: 100, error: null } }

  it('uses a message\'s own linked row, the nearest earlier evaluation without one, and a transition\'s own values directly', () => {
    expect(signalValuesFor({ kind: 'message', message: message(3, '2026-01-01T10:00:02.000') }, log)).toEqual(RISK_100)
    expect(signalValuesFor({ kind: 'message', message: message(4, '2026-01-01T10:00:02.200', 'assistant') }, log)).toEqual(RISK_100)
    expect(signalValuesFor({ kind: 'transition', transition: log[1] }, log)).toEqual(RISK_100)

    // A synthetic/manual transition with no real values of its own reads as n/a.
    const synthetic = { id: null, old_state: '', new_state: 'action', values: null, message_id: 1 }
    expect(signalValuesFor({ kind: 'transition', transition: synthetic }, log)).toEqual({})
  })
})

describe('an imported session — signals/state resolve by transcript position, not by real annotation time', () => {
  // Reproduces the reported bug: reviewing an imported session (see
  // session_import.py — every message has timestamp=null), an expert
  // annotates message 1 first, then later (in real wall-clock time)
  // annotates message 4 (see TrackingService._materialize_imported_
  // session_row — each Tracking row's own `timestamp` is stamped at
  // *annotation* time, unrelated to the message's own position in the
  // transcript). Selecting the unannotated message 3, in between, must
  // show message 1's own signal values (the last one *before* it in the
  // transcript), never message 4's (the globally last-annotated one).
  const m0 = message(0, null) // before any annotation at all
  const m3 = message(3, null) // unannotated, selected below
  const messages = [m0, message(1, null), m3, message(4, null)]
  const log = [
    transitionRow(10, {
      timestamp: '2026-01-01T09:00:00', // annotated first, in real time
      values: JSON.stringify({ mood: 10 }), messageId: 1, expectedState: 'early-state'
    }),
    transitionRow(11, {
      timestamp: '2026-01-01T09:05:00', // annotated later, in real time
      values: JSON.stringify({ mood: 90 }), messageId: 4, expectedState: 'late-state'
    })
  ]

  it('shows the last annotation before the selection in transcript order, for values and for state highlighting alike', () => {
    expect(signalValuesFor({ kind: 'message', message: m3 }, log, messages)).toEqual({ mood: { value: 10, error: null } })
    // A selection before any annotation at all shows nothing, never a later one.
    expect(signalValuesFor({ kind: 'message', message: m0 }, log, messages)).toEqual({})

    const timeline = buildTimeline(messages, log, null, { imported: true })
    expect(highlightedStateKeyFor({ kind: 'message', message: m3 }, timeline, null)).toBe('early-state')
  })
})

describe('highlightedStateKeyFor / resultingStateKeyFor — the same off-by-one, for state', () => {
  const riskyMessage = message(3, '2026-01-01T10:00:02.000')
  const earlier = transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })
  const caused = transitionRow(2, { timestamp: '2026-01-01T10:00:02.100', oldState: 'action', newState: 'crisis', messageId: 3 })
  const timeline = [
    { kind: 'transition', timestamp: earlier.timestamp, transition: earlier },
    { kind: 'message', timestamp: riskyMessage.timestamp, message: riskyMessage },
    { kind: 'transition', timestamp: caused.timestamp, transition: caused }
  ]

  it('highlights the state a message was written in, while resultingState reports where its own turn left things', () => {
    const selected = { kind: 'message', message: riskyMessage }

    expect(highlightedStateKeyFor(selected, timeline, 'lobby')).toBe('action')
    expect(resultingStateKeyFor(selected, timeline, 'lobby')).toBe('crisis')
  })

  it('both fall back to stateAsOf for an unlinked message, use a selected transition\'s own new_state, and are null with nothing selected', () => {
    const unlinked = { kind: 'message', message: message(9, '2026-01-01T09:30:00') }
    const earlierOnly = [timeline[0]]
    expect(highlightedStateKeyFor(unlinked, earlierOnly, 'lobby')).toBe('action')
    expect(resultingStateKeyFor(unlinked, earlierOnly, 'lobby')).toBe('action')

    const selectedTransition = { kind: 'transition', transition: earlier }
    expect(highlightedStateKeyFor(selectedTransition, [], 'lobby')).toBe('action')
    expect(resultingStateKeyFor(selectedTransition, [], 'lobby')).toBe('action')

    expect(highlightedStateKeyFor(null, [], 'lobby')).toBeNull()
    expect(resultingStateKeyFor(null, [], 'lobby')).toBeNull()
  })
})

describe('stateAsOf / actualStateAtOrBefore / nearestMessageIdAtOrBefore', () => {
  it('stateAsOf returns the session start state until a transition precedes the timestamp', () => {
    const timeline = [
      { kind: 'transition', timestamp: '2026-01-01T10:00:00', transition: { new_state: 'action' } },
      { kind: 'transition', timestamp: '2026-01-01T10:05:00', transition: { new_state: 'crisis' } }
    ]

    expect(stateAsOf([], 'lobby', '2026-01-01T10:00:00')).toBe('lobby')
    expect(stateAsOf(timeline, 'lobby', '2026-01-01T10:02:00')).toBe('action')
  })

  it('actualStateAtOrBefore ignores self-loops and rows past the timestamp', () => {
    const log = [
      transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' }),
      transitionRow(2, { timestamp: '2026-01-01T09:30:00', oldState: 'action', newState: 'action' }), // self-loop
      transitionRow(3, { timestamp: '2026-01-01T11:00:00', oldState: 'action', newState: 'crisis' }) // after cutoff
    ]

    expect(actualStateAtOrBefore([], 'lobby', '2026-01-01T10:00:00')).toBe('lobby')
    expect(actualStateAtOrBefore(log, 'lobby', '2026-01-01T10:00:00')).toBe('action')
  })

  it('nearestMessageIdAtOrBefore returns the latest message at or before the timestamp, null when none precedes it', () => {
    const messages = [message(1, '2026-01-01T10:00:00'), message(2, '2026-01-01T10:05:00')]

    expect(nearestMessageIdAtOrBefore(messages, '2026-01-01T10:03:00')).toBe(1)
    expect(nearestMessageIdAtOrBefore(messages, '2026-01-01T10:05:00')).toBe(2)
    expect(nearestMessageIdAtOrBefore(messages, '2026-01-01T09:00:00')).toBeNull()
  })
})

describe('effectiveTimestamp', () => {
  it("uses a linked transition's message timestamp, falling back to its own raw one, and a message's own timestamp as-is", () => {
    const linkedMessage = message(3, '2026-01-01T10:00:02.000')
    const linked = { kind: 'transition', transition: { timestamp: '2026-01-01T10:00:02.100', message_id: 3 } }
    const unlinked = { kind: 'transition', transition: { timestamp: '2026-01-01T10:00:00', message_id: null } }

    expect(effectiveTimestamp(linked, [linkedMessage])).toBe('2026-01-01T10:00:02.000')
    expect(effectiveTimestamp(unlinked, [])).toBe('2026-01-01T10:00:00')
    expect(effectiveTimestamp({ kind: 'message', message: message(1, '2026-01-01T10:00:00') }, [])).toBe('2026-01-01T10:00:00')
  })
})

describe('transitionAnnotationStatus', () => {
  it('compares expected against actual, but reads any annotated imported row as "labelled" and any unannotated one as null', () => {
    expect(transitionAnnotationStatus({ expected_state: null, new_state: 'a' })).toBeNull()
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: 'a' })).toBe('correct')
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: 'b' })).toBe('incorrect')

    for (const newState of ['a', 'b', null]) {
      expect(transitionAnnotationStatus({ expected_state: 'a', new_state: newState }, { imported: true })).toBe('labelled')
    }
    expect(transitionAnnotationStatus({ expected_state: null, new_state: null }, { imported: true })).toBeNull()
  })
})

describe('resolveTransitionRow', () => {
  it('leaves a real transition untouched and fills the actual unchanged state into both sides of an annotated snapshot', () => {
    const real = transitionRow(1, { timestamp: '2026-01-01T10:00:00', oldState: 'action', newState: 'crisis' })
    expect(resolveTransitionRow(real, [], 'lobby')).toBe(real)

    const log = [transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })]
    // A plain auto-tracking snapshot: old_state/new_state both null, but annotated.
    const snapshot = transitionRow(2, { timestamp: '2026-01-01T10:00:00', expectedState: 'action' })

    const resolved = resolveTransitionRow(snapshot, log, 'lobby')

    expect(resolved.old_state).toBe('action')
    expect(resolved.new_state).toBe('action')
    // The expert said "action" and that's genuinely what was in effect.
    expect(transitionAnnotationStatus(resolved)).toBe('correct')
  })

  it('an imported row resolves new_state straight to its own expected_state, ignoring sessionStartState/signalsLog entirely', () => {
    // Imported sessions never have a real avance-computed new_state at
    // all (see TrackingService._materialize_imported_session_row's own
    // save_transition(None, None, None, ...)) — actualStateAtOrBefore
    // would just keep returning sessionStartState (itself null for an
    // import) for every row, so it must never be consulted here.
    const resolved = resolveTransitionRow(
      transitionRow(1, { timestamp: '2026-01-01T10:00:00', expectedState: 'action' }), [], null, { imported: true }
    )

    expect(resolved.old_state).toBeNull()
    expect(resolved.new_state).toBe('action')
  })
})

describe('syntheticSessionStartEntry', () => {
  it('builds an unannotated, valueless transition anchored to the first message, and nothing at all when unneeded', () => {
    // Regression test: this entry must never carry stale/unrelated values —
    // see signalValuesFor's own "n/a, not the last state's data" test above,
    // which is exactly what this shape (no `values` field at all) enables.
    const firstMessage = message(1, '2026-01-01T10:00:00')
    const realStartRow = [transitionRow(1, { timestamp: '2026-01-01T10:00:00', oldState: '', newState: 'lobby' })]

    expect(syntheticSessionStartEntry(realStartRow, [firstMessage], 'lobby')).toBeNull()
    expect(syntheticSessionStartEntry([], [], 'lobby')).toBeNull()

    const entry = syntheticSessionStartEntry([], [firstMessage], 'lobby')
    expect(entry.transition).toEqual({
      id: null,
      old_state: '',
      action: '',
      new_state: 'lobby',
      expected_state: null,
      expected_values: null,
      message_id: 1
    })
    expect(entry.timestamp).toBe(firstMessage.timestamp)
  })
})

describe('autotracking_on_ai_message=False — the reported separator/signal misplacement bug', () => {
  // Reproduces the exact scenario reported against Edit Project's live
  // chat: "before" mode (autotracking_on_ai_message=False) decides its
  // trigger from the *user's* message, before the assistant even replies
  // — so the backend now links the resulting Tracking row to the user
  // message, not the assistant's. This locks down that, given that
  // correct link, both the separator's position and the Inspector's
  // signal values come out right without this module needing any change
  // of its own — the bug was backend-side (the wrong message_id).
  const userMsg = message(2, '2026-01-01T10:00:05', 'user')
  const messages = [userMsg, message(3, '2026-01-01T10:00:07', 'assistant')]
  const transition = transitionRow(10, {
    timestamp: '2026-01-01T10:00:05.500', // after the user message, before the AI reply
    oldState: 'a', newState: 'b', values: JSON.stringify({ mySignal: 1 }),
    messageId: userMsg.id // linked to the CAUSING (user) message, not the AI one
  })
  const log = [transitionRow(9, { timestamp: '2026-01-01T09:59:59', oldState: '', newState: 'a' }), transition]
  const SIGNAL_1 = { mySignal: { value: 1, error: null } }

  it('places the separator right after the user message, and the Inspector shows its values from either selection', () => {
    expect(kinds(buildTimeline(messages, log, 'a'))).toEqual(['t', 'm2', 't', 'm3'])
    expect(signalValuesFor({ kind: 'transition', transition }, log)).toEqual(SIGNAL_1)
    expect(signalValuesFor({ kind: 'message', message: userMsg }, log)).toEqual(SIGNAL_1)
  })
})

describe('buildTimeline', () => {
  // Every log below includes its own real "" -> start_state row so
  // syntheticSessionStartEntry never adds an extra one — that entry has
  // its own dedicated tests above.
  const startRow = (id, timestamp) => transitionRow(id, { timestamp, oldState: '', newState: 'lobby' })

  it('merges messages and transitions chronologically, sorting a linked transition right after the message it belongs to', () => {
    // The transition's own raw timestamp is *after* message 2's — see
    // effectiveTimestamp's own docstring — but it must still land right
    // after message 2, not after some later message.
    const messages = [
      message(1, '2026-01-01T10:00:00'),
      message(2, '2026-01-01T10:00:05'),
      message(3, '2026-01-01T10:00:10')
    ]
    const log = [
      startRow(1, '2026-01-01T09:59:59'),
      transitionRow(2, { timestamp: '2026-01-01T10:00:05.500', oldState: 'lobby', newState: 'action', messageId: 2 })
    ]

    expect(kinds(buildTimeline(messages, log, 'lobby'))).toEqual(['t', 'm1', 'm2', 't', 'm3'])
  })

  // Regression test: reported against "Aprendr català" — the init
  // transition ("" -> welcome) is linked to the welcome state's own
  // *opening* bubble (it's the effect of entering that state, not its
  // cause — see open_if_needed's own docstring), so it shares that
  // message's effective timestamp. The general "message reads first"
  // tie-break is right for an ordinary transition (whose linked message
  // *caused* it) but backwards here.
  it('sorts the init transition, real or synthetic, before its own linked opening message', () => {
    const opening = message(1, '2026-01-01T10:00:00')
    const real = transitionRow(1, { timestamp: '2026-01-01T10:00:00', oldState: '', newState: 'welcome', messageId: 1 })

    expect(buildTimeline([opening], [real], 'welcome').map((e) => e.kind)).toEqual(['transition', 'message'])
    expect(buildTimeline([opening], [], 'welcome').map((e) => e.kind)).toEqual(['transition', 'message'])

    const synthetic = buildTimeline([opening], [], 'lobby').find((e) => e.kind === 'transition')
    expect(synthetic.transition.old_state).toBe('')
    expect(synthetic.transition.new_state).toBe('lobby')
  })

  it('includes a fired self-loop only with includeSelfLoops, an annotated snapshot always, and a plain snapshot never', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]
    const start = startRow(1, '2026-01-01T09:59:59')
    const plainSnapshot = transitionRow(2, { timestamp: '2026-01-01T10:00:01' })
    const annotatedSnapshot = transitionRow(3, { timestamp: '2026-01-01T10:00:02', expectedState: 'action' })
    const selfLoop = transitionRow(4, { timestamp: '2026-01-01T10:00:03', oldState: 'lobby', newState: 'lobby' })

    expect(transitionIds(buildTimeline(messages, [start, plainSnapshot, annotatedSnapshot], 'lobby'))).toEqual([1, 3])
    expect(transitionIds(buildTimeline(messages, [start, selfLoop], 'lobby'))).toEqual([1])
    expect(transitionIds(buildTimeline(messages, [start, selfLoop], 'lobby', { includeSelfLoops: true }))).toEqual([1, 4])
    expect(transitionIds(buildTimeline(messages, [start, plainSnapshot], 'lobby', { includeSelfLoops: true }))).toEqual([1])
  })

  describe('an imported session (no real timestamps at all — see session_import.py)', () => {
    // Every message/transition timestamp is null (see effectiveTimestamp's
    // own null collapse for an import) — without a message-id fallback,
    // every annotated separator sorts *after every message* instead of
    // right after the one it annotates (the reported bug).
    const importedMessages = [message(1, null), message(2, null), message(3, null)]

    it('pins every annotated separator right after its own linked message, each reading as "labelled"', () => {
      const annotation = transitionRow(1, { timestamp: null, expectedState: 'action', messageId: 2 })

      const timeline = buildTimeline(importedMessages, [annotation], null, { imported: true })

      expect(kinds(timeline)).toEqual(['m1', 'm2', 't', 'm3'])
      const transitionEntry = timeline.find((e) => e.kind === 'transition')
      expect(transitionEntry.annotationStatus).toBe('labelled')
      expect(transitionEntry.transition.new_state).toBe('action')

      const first = transitionRow(1, { timestamp: null, expectedState: 'a', messageId: 1 })
      const second = transitionRow(2, { timestamp: null, expectedState: 'b', messageId: 3 })
      expect(kinds(buildTimeline(importedMessages, [first, second], null, { imported: true })))
        .toEqual(['m1', 't', 'm2', 'm3', 't'])
    })
  })
})
