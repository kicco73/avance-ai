import { describe, expect, it } from 'vitest'
import {
  actualStateAtOrBefore,
  buildTimeline,
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
} from '../src/benchmarkTimeline.js'

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

describe('valuesToSignalValues', () => {
  it('returns an empty object for null/undefined', () => {
    expect(valuesToSignalValues(null)).toEqual({})
    expect(valuesToSignalValues(undefined)).toEqual({})
  })

  it('reshapes a plain {name: value} JSON string into {name: {value, error}}', () => {
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
  // own timestamp (see signalValuesFor), never a message's directly —
  // this just locks down its own at-or-before selection logic.
  it('picks the latest row at or before the given timestamp, ignoring rows without values', () => {
    const log = [
      transitionRow(1, { timestamp: '2026-01-01T10:00:00', values: JSON.stringify({ risk: 0 }) }),
      transitionRow(2, { timestamp: '2026-01-01T10:00:05', values: null }),
      transitionRow(3, { timestamp: '2026-01-01T10:00:10', values: JSON.stringify({ risk: 100 }) })
    ]

    expect(signalValuesAsOf(log, '2026-01-01T10:00:07')).toEqual({ risk: { value: 0, error: null } })
    expect(signalValuesAsOf(log, '2026-01-01T10:00:10')).toEqual({ risk: { value: 100, error: null } })
  })

  it('returns an empty object when nothing precedes the given timestamp', () => {
    const log = [transitionRow(1, { timestamp: '2026-01-01T10:00:10', values: JSON.stringify({ risk: 100 }) })]

    expect(signalValuesAsOf(log, '2026-01-01T10:00:00')).toEqual({})
  })
})

describe('signalValuesFor — the off-by-one bug this session found and fixed', () => {
  // Reproduces the exact scenario reported: a risky user message (id=3)
  // whose own evaluation (Signals row id=2, message_id=3) is timestamped
  // *after* the message itself, because auto-tracking only ever runs
  // once its own triggering message is already saved. Selecting that
  // message must show *its own* evaluation's values (100), never the
  // previous point's (0) — see this session's own empirical reproduction
  // against the real ChatService/AutoTracker.
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
  const riskyMessage = message(3, '2026-01-01T10:00:02.000')

  it("uses the message's own directly-linked row, not a timestamp scan", () => {
    const selected = { kind: 'message', message: riskyMessage }

    expect(signalValuesFor(selected, log)).toEqual({ detectRisk: { value: 100, error: null } })
  })

  it('falls back to the nearest real evaluation before it when the message has no linked row', () => {
    const unlinkedAssistantMessage = message(4, '2026-01-01T10:00:02.200', 'assistant')
    const selected = { kind: 'message', message: unlinkedAssistantMessage }

    expect(signalValuesFor(selected, log)).toEqual({ detectRisk: { value: 100, error: null } })
  })

  it('a selected transition uses its own row values directly, never a timestamp lookup', () => {
    const selected = { kind: 'transition', transition: log[1] }

    expect(signalValuesFor(selected, log)).toEqual({ detectRisk: { value: 100, error: null } })
  })

  it('a synthetic/manual transition with no real values of its own reads as n/a', () => {
    const synthetic = { id: null, old_state: '', new_state: 'action', values: null, message_id: 1 }
    const selected = { kind: 'transition', transition: synthetic }

    expect(signalValuesFor(selected, log)).toEqual({})
  })
})

describe('highlightedStateKeyFor — same off-by-one, for state highlighting', () => {
  it("a message stays in the state active when it was written, even when its own evaluation later causes a transition", () => {
    const earlier = transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })
    const riskyMessage = message(3, '2026-01-01T10:00:02.000')
    const timeline = [
      { kind: 'transition', timestamp: earlier.timestamp, transition: earlier },
      { kind: 'message', timestamp: riskyMessage.timestamp, message: riskyMessage },
      {
        kind: 'transition',
        timestamp: '2026-01-01T10:00:02.100',
        transition: transitionRow(2, { timestamp: '2026-01-01T10:00:02.100', oldState: 'action', newState: 'crisis', messageId: 3 })
      }
    ]
    const selected = { kind: 'message', message: riskyMessage }

    expect(highlightedStateKeyFor(selected, timeline, 'lobby')).toBe('action')
  })

  it('falls back to stateAsOf when no transition is linked to the selected message', () => {
    const earlier = transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })
    const timeline = [{ kind: 'transition', timestamp: earlier.timestamp, transition: earlier }]
    const unlinkedMessage = message(9, '2026-01-01T09:30:00')
    const selected = { kind: 'message', message: unlinkedMessage }

    expect(highlightedStateKeyFor(selected, timeline, 'lobby')).toBe('action')
  })

  it('a selected transition always highlights its own new_state', () => {
    const t = transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })
    const selected = { kind: 'transition', transition: t }

    expect(highlightedStateKeyFor(selected, [], 'lobby')).toBe('action')
  })

  it('returns null with nothing selected', () => {
    expect(highlightedStateKeyFor(null, [], 'lobby')).toBeNull()
  })
})

describe('resultingStateKeyFor — where a message\'s own turn ultimately left things', () => {
  it("prefers the state a directly-linked transition produced over the raw-timestamp fallback", () => {
    const riskyMessage = message(3, '2026-01-01T10:00:02.000')
    const timeline = [
      { kind: 'message', timestamp: riskyMessage.timestamp, message: riskyMessage },
      {
        kind: 'transition',
        timestamp: '2026-01-01T10:00:02.100',
        transition: transitionRow(2, { timestamp: '2026-01-01T10:00:02.100', oldState: 'action', newState: 'crisis', messageId: 3 })
      }
    ]
    const selected = { kind: 'message', message: riskyMessage }

    expect(resultingStateKeyFor(selected, timeline, 'lobby')).toBe('crisis')
  })

  it('falls back to stateAsOf when no transition is linked to the selected message', () => {
    const earlier = transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })
    const timeline = [{ kind: 'transition', timestamp: earlier.timestamp, transition: earlier }]
    const unlinkedMessage = message(9, '2026-01-01T09:30:00')
    const selected = { kind: 'message', message: unlinkedMessage }

    expect(resultingStateKeyFor(selected, timeline, 'lobby')).toBe('action')
  })

  it('a selected transition always resolves to its own new_state', () => {
    const t = transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })
    const selected = { kind: 'transition', transition: t }

    expect(resultingStateKeyFor(selected, [], 'lobby')).toBe('action')
  })

  it('returns null with nothing selected', () => {
    expect(resultingStateKeyFor(null, [], 'lobby')).toBeNull()
  })
})

describe('stateAsOf', () => {
  it('returns the session start state before any transition', () => {
    expect(stateAsOf([], 'lobby', '2026-01-01T10:00:00')).toBe('lobby')
  })

  it('returns the latest transition at or before the timestamp, ignoring later ones', () => {
    const timeline = [
      { kind: 'transition', timestamp: '2026-01-01T10:00:00', transition: { new_state: 'action' } },
      { kind: 'transition', timestamp: '2026-01-01T10:05:00', transition: { new_state: 'crisis' } }
    ]

    expect(stateAsOf(timeline, 'lobby', '2026-01-01T10:02:00')).toBe('action')
  })
})

describe('nearestMessageIdAtOrBefore', () => {
  it('returns the id of the latest message at or before the timestamp', () => {
    const messages = [message(1, '2026-01-01T10:00:00'), message(2, '2026-01-01T10:05:00')]

    expect(nearestMessageIdAtOrBefore(messages, '2026-01-01T10:03:00')).toBe(1)
    expect(nearestMessageIdAtOrBefore(messages, '2026-01-01T10:05:00')).toBe(2)
  })

  it('returns null when no message precedes the timestamp', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]

    expect(nearestMessageIdAtOrBefore(messages, '2026-01-01T09:00:00')).toBeNull()
  })
})

describe('effectiveTimestamp', () => {
  it("uses a linked transition's message timestamp instead of its own raw one", () => {
    const linkedMessage = message(3, '2026-01-01T10:00:02.000')
    const entry = {
      kind: 'transition',
      transition: { timestamp: '2026-01-01T10:00:02.100', message_id: 3 }
    }

    expect(effectiveTimestamp(entry, [linkedMessage])).toBe('2026-01-01T10:00:02.000')
  })

  it('falls back to the raw timestamp for a transition with no linked message', () => {
    const entry = { kind: 'transition', transition: { timestamp: '2026-01-01T10:00:00', message_id: null } }

    expect(effectiveTimestamp(entry, [])).toBe('2026-01-01T10:00:00')
  })

  it("uses the message's own timestamp for a message entry", () => {
    const entry = { kind: 'message', message: message(1, '2026-01-01T10:00:00') }

    expect(effectiveTimestamp(entry, [])).toBe('2026-01-01T10:00:00')
  })
})

describe('transitionAnnotationStatus', () => {
  it('is null when unannotated', () => {
    expect(transitionAnnotationStatus({ expected_state: null, new_state: 'a' })).toBeNull()
  })

  it('is correct when the expected state matches what actually happened', () => {
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: 'a' })).toBe('correct')
  })

  it('is incorrect when the expected state differs', () => {
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: 'b' })).toBe('incorrect')
  })

  it('is labelled (never correct/incorrect) for an imported session, whatever new_state says', () => {
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: 'a' }, { imported: true })).toBe('labelled')
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: 'b' }, { imported: true })).toBe('labelled')
    expect(transitionAnnotationStatus({ expected_state: 'a', new_state: null }, { imported: true })).toBe('labelled')
  })

  it('stays null for an unannotated imported row', () => {
    expect(transitionAnnotationStatus({ expected_state: null, new_state: null }, { imported: true })).toBeNull()
  })
})

describe('actualStateAtOrBefore', () => {
  it('returns the session start state with no real transitions yet', () => {
    expect(actualStateAtOrBefore([], 'lobby', '2026-01-01T10:00:00')).toBe('lobby')
  })

  it('ignores self-loops (old_state === new_state) and rows past the timestamp', () => {
    const log = [
      transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' }),
      transitionRow(2, { timestamp: '2026-01-01T09:30:00', oldState: 'action', newState: 'action' }), // self-loop
      transitionRow(3, { timestamp: '2026-01-01T11:00:00', oldState: 'action', newState: 'crisis' }) // after cutoff
    ]

    expect(actualStateAtOrBefore(log, 'lobby', '2026-01-01T10:00:00')).toBe('action')
  })
})

describe('resolveTransitionRow', () => {
  it('returns a real transition (old_state !== new_state) untouched', () => {
    const row = transitionRow(1, { timestamp: '2026-01-01T10:00:00', oldState: 'action', newState: 'crisis' })

    expect(resolveTransitionRow(row, [], 'lobby')).toBe(row)
  })

  it('fills in the actual unchanged state on both sides for a no-real-change row', () => {
    const log = [transitionRow(1, { timestamp: '2026-01-01T09:00:00', oldState: 'lobby', newState: 'action' })]
    // A plain auto-tracking snapshot: old_state/new_state both null, but
    // annotated (see the "no state change" bug this session fixed).
    const snapshot = transitionRow(2, { timestamp: '2026-01-01T10:00:00', expectedState: 'action' })

    const resolved = resolveTransitionRow(snapshot, log, 'lobby')

    expect(resolved.old_state).toBe('action')
    expect(resolved.new_state).toBe('action')
    // transitionAnnotationStatus must read this as "correct" — the expert
    // said "action" and that's genuinely what was in effect.
    expect(transitionAnnotationStatus(resolved)).toBe('correct')
  })

  it('an imported row resolves new_state straight to its own expected_state, ignoring sessionStartState/signalsLog entirely', () => {
    // Imported sessions never have a real avance-computed new_state at
    // all (see TrackingService._materialize_imported_session_row's own
    // save_transition(None, None, None, ...)) — actualStateAtOrBefore
    // would just keep returning sessionStartState (itself null for an
    // import) for every row, so it must never be consulted here.
    const row = transitionRow(1, { timestamp: '2026-01-01T10:00:00', expectedState: 'action' })

    const resolved = resolveTransitionRow(row, [], null, { imported: true })

    expect(resolved.old_state).toBeNull()
    expect(resolved.new_state).toBe('action')
  })
})

describe('syntheticSessionStartEntry', () => {
  it('is null when the session already has its own real start row', () => {
    const log = [transitionRow(1, { timestamp: '2026-01-01T10:00:00', oldState: '', newState: 'lobby' })]
    const messages = [message(1, '2026-01-01T10:00:00')]

    expect(syntheticSessionStartEntry(log, messages, 'lobby')).toBeNull()
  })

  it('is null with no messages yet', () => {
    expect(syntheticSessionStartEntry([], [], 'lobby')).toBeNull()
  })

  // Regression test: this entry must never carry stale/unrelated values —
  // see signalValuesFor's own "n/a, not the last state's data" test above,
  // which is exactly what this shape (no `values` field at all) enables.
  it('builds an unannotated, valueless transition anchored to the first message', () => {
    const firstMessage = message(1, '2026-01-01T10:00:00')

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
  // message (see backend tests/test_chat_service_evaluation_points.py's
  // test_transition_from_optimistic_guess_links_the_causing_user_message),
  // not the assistant's. This locks down that, given that correct link,
  // both the separator's position and the Inspector's signal values come
  // out right without this module needing any change of its own — the
  // bug was backend-side (the wrong message_id), not in this sorting/
  // lookup logic.
  const userMsg = message(2, '2026-01-01T10:00:05', 'user')
  const aiMsg = message(3, '2026-01-01T10:00:07', 'assistant')
  // A real "" -> a session-start row, same as buildTimeline's other tests
  // use, so syntheticSessionStartEntry doesn't add its own extra one.
  const start = transitionRow(9, { timestamp: '2026-01-01T09:59:59', oldState: '', newState: 'a' })
  const transition = transitionRow(10, {
    timestamp: '2026-01-01T10:00:05.500', // after the user message, before the AI reply
    oldState: 'a',
    newState: 'b',
    values: JSON.stringify({ mySignal: 1 }),
    messageId: userMsg.id // linked to the CAUSING (user) message, not the AI one
  })
  const log = [start, transition]
  const messages = [userMsg, aiMsg]

  it('places the state-change separator right after the user message, before the AI reply', () => {
    const timeline = buildTimeline(messages, log, 'a')

    expect(timeline.map((e) => (e.kind === 'message' ? `m${e.message.id}` : 't'))).toEqual(['t', 'm2', 't', 'm3'])
  })

  it("the Inspector shows the transition's own signal values when the separator is selected", () => {
    const selected = { kind: 'transition', transition }

    expect(signalValuesFor(selected, log)).toEqual({ mySignal: { value: 1, error: null } })
  })

  it("the Inspector shows the same signal values when the causing user message is selected directly", () => {
    const selected = { kind: 'message', message: userMsg }

    expect(signalValuesFor(selected, log)).toEqual({ mySignal: { value: 1, error: null } })
  })
})

describe('buildTimeline', () => {
  // Every log below includes its own real "" -> start_state row so
  // syntheticSessionStartEntry never adds an extra one — that entry has
  // its own dedicated tests below.
  const startRow = (id, timestamp) => transitionRow(id, { timestamp, oldState: '', newState: 'lobby' })

  it('merges messages and real transitions in chronological order', () => {
    const messages = [message(1, '2026-01-01T10:00:00'), message(2, '2026-01-01T10:00:05')]
    const log = [
      startRow(1, '2026-01-01T09:59:59'),
      transitionRow(2, { timestamp: '2026-01-01T10:00:05.500', oldState: 'lobby', newState: 'action', messageId: 2 })
    ]

    const timeline = buildTimeline(messages, log, 'lobby')

    expect(timeline.map((e) => e.kind)).toEqual(['transition', 'message', 'message', 'transition'])
  })

  it('sorts a linked transition right after the message it belongs to, at the same effective moment', () => {
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

    const timeline = buildTimeline(messages, log, 'lobby')

    expect(timeline.map((e) => (e.kind === 'message' ? `m${e.message.id}` : 't'))).toEqual(['t', 'm1', 'm2', 't', 'm3'])
  })

  // Regression test: reported against "Aprendr català" — the init
  // transition ("" -> welcome) is linked to the welcome state's own
  // *opening* bubble (it's the effect of entering that state, not its
  // cause — see open_if_needed's own docstring), so it shares that
  // message's effective timestamp. The general "message reads first"
  // tie-break is right for an ordinary transition (whose linked message
  // *caused* it) but backwards here: the transition (arriving at
  // "welcome") must read before the bubble generated for being there.
  it('sorts the init transition (real or synthetic) before its own linked opening message, not after', () => {
    const opening = message(1, '2026-01-01T10:00:00')
    const real = transitionRow(1, { timestamp: '2026-01-01T10:00:00', oldState: '', newState: 'welcome', messageId: 1 })

    const timeline = buildTimeline([opening], [real], 'welcome')

    expect(timeline.map((e) => e.kind)).toEqual(['transition', 'message'])
  })

  it('sorts the synthetic session-start entry before the first message too', () => {
    const opening = message(1, '2026-01-01T10:00:00')

    const timeline = buildTimeline([opening], [], 'welcome')

    expect(timeline.map((e) => e.kind)).toEqual(['transition', 'message'])
  })

  it('excludes an unannotated self-loop/plain snapshot, but includes an annotated one', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]
    const start = startRow(1, '2026-01-01T09:59:59')
    const unannotatedSnapshot = transitionRow(2, { timestamp: '2026-01-01T10:00:01' })
    const annotatedSnapshot = transitionRow(3, { timestamp: '2026-01-01T10:00:02', expectedState: 'action' })

    const timeline = buildTimeline(messages, [start, unannotatedSnapshot, annotatedSnapshot], 'lobby')

    const transitionIds = timeline.filter((e) => e.kind === 'transition').map((e) => e.transition.id)
    expect(transitionIds).toEqual([1, 3])
  })

  it('still excludes a plain snapshot (no action fired at all) even with includeSelfLoops', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]
    const start = startRow(1, '2026-01-01T09:59:59')
    const plainSnapshot = transitionRow(2, { timestamp: '2026-01-01T10:00:01' })

    const timeline = buildTimeline(messages, [start, plainSnapshot], 'lobby', { includeSelfLoops: true })

    const transitionIds = timeline.filter((e) => e.kind === 'transition').map((e) => e.transition.id)
    expect(transitionIds).toEqual([1])
  })

  it('includes a fired, unannotated self-loop when includeSelfLoops is set', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]
    const start = startRow(1, '2026-01-01T09:59:59')
    const selfLoop = transitionRow(2, { timestamp: '2026-01-01T10:00:01', oldState: 'lobby', newState: 'lobby' })

    const timeline = buildTimeline(messages, [start, selfLoop], 'lobby', { includeSelfLoops: true })

    const transitionIds = timeline.filter((e) => e.kind === 'transition').map((e) => e.transition.id)
    expect(transitionIds).toEqual([1, 2])
  })

  it('still excludes an unannotated self-loop by default (includeSelfLoops omitted)', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]
    const start = startRow(1, '2026-01-01T09:59:59')
    const selfLoop = transitionRow(2, { timestamp: '2026-01-01T10:00:01', oldState: 'lobby', newState: 'lobby' })

    const timeline = buildTimeline(messages, [start, selfLoop], 'lobby')

    const transitionIds = timeline.filter((e) => e.kind === 'transition').map((e) => e.transition.id)
    expect(transitionIds).toEqual([1])
  })

  describe('an imported session (no real timestamps at all — see session_import.py)', () => {
    // Every message/transition timestamp is null (see effectiveTimestamp's
    // own null collapse for an import) — without a message-id fallback,
    // every annotated separator sorts *after every message* instead of
    // right after the one it annotates (the reported bug).
    const importedMessages = [message(1, null), message(2, null), message(3, null)]

    it('places each annotated separator right after its own linked message, not appended at the end', () => {
      const annotation = transitionRow(1, { timestamp: null, expectedState: 'action', messageId: 2 })

      const timeline = buildTimeline(importedMessages, [annotation], null, { imported: true })

      expect(timeline.map((e) => (e.kind === 'message' ? `m${e.message.id}` : 't'))).toEqual(['m1', 'm2', 't', 'm3'])
    })

    it('keeps multiple annotated separators each pinned right after their own message', () => {
      const first = transitionRow(1, { timestamp: null, expectedState: 'a', messageId: 1 })
      const second = transitionRow(2, { timestamp: null, expectedState: 'b', messageId: 3 })

      const timeline = buildTimeline(importedMessages, [first, second], null, { imported: true })

      expect(timeline.map((e) => (e.kind === 'message' ? `m${e.message.id}` : 't'))).toEqual(['m1', 't', 'm2', 'm3', 't'])
    })

    it("every entry's own annotationStatus is 'labelled', never correct/incorrect", () => {
      const annotation = transitionRow(1, { timestamp: null, expectedState: 'action', messageId: 2 })

      const timeline = buildTimeline(importedMessages, [annotation], null, { imported: true })

      const transitionEntry = timeline.find((e) => e.kind === 'transition')
      expect(transitionEntry.annotationStatus).toBe('labelled')
      expect(transitionEntry.transition.new_state).toBe('action')
    })
  })

  it('appends the synthetic session-start entry when the session has no real one', () => {
    const messages = [message(1, '2026-01-01T10:00:00')]

    const timeline = buildTimeline(messages, [], 'lobby')

    const synthetic = timeline.find((e) => e.kind === 'transition')
    expect(synthetic.transition.old_state).toBe('')
    expect(synthetic.transition.new_state).toBe('lobby')
  })
})
