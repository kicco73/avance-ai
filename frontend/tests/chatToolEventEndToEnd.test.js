// End-to-end: a live turn's own frames on the chat websocket — one 'tool'
// frame on phase "start" (with status_text and the structured fields), one
// on phase "result", three chunks, done — driven through chatClient.js's
// REAL frame dispatch (only api.js's createChatSocket is faked), all the
// way up into the chat store and MessageBubble-facing message shape.
// Proves the whole pipe end to end: while the tool call is in flight the
// bubble shows status_text, the "result" phase clears it once
// TOOL_STATUS_MIN_MS is up (even past "done"), chunks accumulate, and once
// the turn is done the persisted tool_calls record (fetched via
// getMessages, same as a reload) renders through toolTraceLine.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toolTraceLine } from '../src/toolTraceLine.js'
import { TOOL_STATUS_MIN_MS } from '../src/toolStatusHold.js'
import { installFakeChatSocket, turnIdOf } from './fakeChatSocket.js'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
  createChatSocket: vi.fn(),
}))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn(), clearApiError: vi.fn() }))

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
  let chatClient
  let api
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    chatClient.connect()
    sockets[0].open()
  })

  afterEach(() => {
    chatClient.disconnect()
    vi.clearAllMocks()
  })

  it('streams status_text, clears it, accumulates chunks, then shows the persisted trace', async () => {
    chatStore.currentSessionId.value = 1
    api.getMessages.mockResolvedValue([
      { id: 51, role: 'assistant', content: 'Your flight is on time.', timestamp: 't', tool_calls: [PERSISTED_RECORD] }
    ])

    const sendPromise = chatStore.handleSend('where is my flight?')
    await vi.waitFor(() => expect(sockets[0].sent.some((f) => f.type === 'turn')).toBe(true))
    const turnId = turnIdOf(sockets[0])
    const socket = sockets[0]

    socket.emit({ type: 'tool', turn_id: turnId, ...TOOL_START })
    await vi.waitFor(() => {
      const msg = chatStore.messages.value.find((m) => m.role === 'assistant')
      expect(msg?.statusText).toBe(TOOL_START.status_text)
    })

    socket.emit({ type: 'tool', turn_id: turnId, ...TOOL_RESULT })
    for (const content of ['Your ', 'flight ', 'is on time.']) {
      socket.emit({ type: 'chunk', turn_id: turnId, content })
    }
    socket.emit({
      type: 'done', turn_id: turnId,
      reply: [{ id: 51, content: 'Your flight is on time.', timestamp: 't' }],
      assistant_message_id: 51, session_id: 1,
    })

    await sendPromise

    const finished = chatStore.messages.value.find((m) => m.messageId === 51)
    // The status line outlives "done" by design — see toolStatusHold.js.
    expect(finished.statusText).toBe(TOOL_START.status_text)
    expect(finished.content).toBe('Your flight is on time.')

    await vi.waitFor(() => {
      expect(chatStore.messages.value.find((m) => m.messageId === 51)?.statusText).toBe('')
    }, { timeout: TOOL_STATUS_MIN_MS + 1000 })

    const traced = () => chatStore.messages.value.find((m) => m.messageId === 51)
    await vi.waitFor(() => {
      expect(traced().toolCalls).toEqual([PERSISTED_RECORD])
    })
    expect(toolTraceLine(traced().toolCalls[0])).toBe('Searched Flight records for "VY3003" · 1 row')
  })
})
