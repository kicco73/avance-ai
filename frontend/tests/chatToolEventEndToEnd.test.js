// End-to-end: a live turn's own SSE stream — tool_call (with status_text
// and the structured fields), tool_result, three chunks, done — driven
// through chatClient.js's REAL readSseTurnStream (only api.js's
// postChatMessage is faked, returning a Response-shaped SSE body), all
// the way up into the chat store and MessageBubble-facing message shape.
// Proves the whole pipe end to end: while the tool call is in flight the
// bubble shows status_text, tool_result clears it, chunks accumulate,
// and once the turn is done the persisted tool_calls record (fetched via
// getMessages, same as a reload) renders through toolTraceLine.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toolTraceLine } from '../src/toolTraceLine.js'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
  postChatMessage: vi.fn(),
  createChatSocket: vi.fn(() => {
    const ws = { onopen: null, onmessage: null, onerror: null, onclose: null }
    queueMicrotask(() => ws.onclose?.())
    return ws
  })
}))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn(), clearApiError: vi.fn() }))

// One encoded SSE block per network read, each behind a real macrotask
// delay (not just a microtask) — matching how a real stream actually
// arrives, chunk by chunk, and letting a caller's own vi.waitFor observe
// the state readSseTurnStream applies after each block rather than only
// once every event has already been processed.
function fakeSseResponse(events) {
  const blocks = events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
  let index = 0
  return {
    body: {
      getReader: () => ({
        read: () => new Promise((resolve) => {
          // Spaced far enough apart that vi.waitFor's own polling interval
          // can observe the state applied after each individual block,
          // not just the state left once every block has already landed.
          setTimeout(() => {
            if (index >= blocks.length) {
              resolve({ done: true, value: undefined })
              return
            }
            resolve({ done: false, value: new TextEncoder().encode(blocks[index++]) })
          }, index * 60)
        })
      })
    }
  }
}

const TOOL_START = {
  phase: 'start', name: 'source_flights_select', source: 'flights', method: 'select',
  label: 'Flight records', description: null, arguments: { values: ['VY3003'] }, round: 1,
  status_text: 'Searching Flight records for "VY3003"…'
}
const TOOL_RESULT = {
  phase: 'result', name: 'source_flights_select', source: 'flights', method: 'select',
  label: 'Flight records', description: null, arguments: { values: ['VY3003'] }, round: 1,
  result: 'city\nParis\n', rows: 1, error: false, duration_ms: 12
}
const PERSISTED_RECORD = {
  name: 'source_flights_select', arguments: { values: ['VY3003'] },
  result: 'city\nParis\n', label: 'Flight records', rows: 1, error: false, duration_ms: 12
}

describe('a live turn shows the tool status then the persisted trace, end to end', () => {
  let chatStore
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('streams status_text, clears it, accumulates chunks, then shows the persisted trace', async () => {
    chatStore.currentSessionId.value = 1
    api.postChatMessage.mockResolvedValue(fakeSseResponse([
      ['tool_call', TOOL_START],
      ['tool_result', TOOL_RESULT],
      ['chunk', { content: 'Your ' }],
      ['chunk', { content: 'flight ' }],
      ['chunk', { content: 'is on time.' }],
      ['done', { reply: [{ id: 51, content: 'Your flight is on time.', timestamp: 't' }], assistant_message_id: 51, session_id: 1 }]
    ]))
    api.getMessages.mockResolvedValue([
      { id: 51, role: 'assistant', content: 'Your flight is on time.', timestamp: 't', tool_calls: [PERSISTED_RECORD] }
    ])

    const sendPromise = chatStore.handleSend('where is my flight?')

    await vi.waitFor(() => {
      const msg = chatStore.messages.value.find((m) => m.role === 'assistant')
      expect(msg?.statusText).toBe(TOOL_START.status_text)
    })

    await sendPromise

    const finished = chatStore.messages.value.find((m) => m.messageId === 51)
    expect(finished.statusText).toBe('')
    expect(finished.content).toBe('Your flight is on time.')

    await vi.waitFor(() => {
      expect(finished.toolCalls).toEqual([PERSISTED_RECORD])
    })
    expect(toolTraceLine(finished.toolCalls[0])).toBe('Searched Flight records for "VY3003" · 1 row')
  })
})
