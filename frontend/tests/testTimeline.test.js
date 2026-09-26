import { describe, expect, it } from 'vitest'
import { buildTimeline } from '../src/testTimeline.js'

const messages = [
  { id: 1, role: 'user', content: 'hi', timestamp: '2026-09-26T09:00:01+00:00' },
  { id: 2, role: 'assistant', content: 'hello', timestamp: '2026-09-26T09:00:01+00:00' },
]

function row(id, position, action) {
  return {
    id, position, action, message_id: null, old_state: 'a', new_state: 'b', expected_state: null,
    timestamp: '2026-09-26T09:30:00+00:00', values: null, expected_values: null,
  }
}

function shape(timeline) {
  return timeline.map((entry) => (entry.kind === 'message' ? entry.message.id : entry.transition.action))
}

describe('the label timeline', () => {
  it('places an action entry at its position among the messages, whatever its timestamp', () => {
    const timeline = buildTimeline(messages, [row(10, 0, 'start'), row(11, 1, 'go'), row(12, 2, 'end')], 'a')

    expect(shape(timeline)).toEqual(['', 'start', 1, 'go', 2, 'end'])
  })
})
