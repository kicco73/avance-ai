// End to end: a live exchange's own frames on the one websocket — one
// `output.tool` on phase "start" (with status_text and the structured
// fields), one on phase "result", the pieces of the message, then the
// message itself — driven through busChannel.js's REAL frame dispatch
// (only api.js's createChatSocket is faked), all the way up into the chat
// store and the MessageBubble-facing message shape. Proves the whole pipe:
// while the tool call is in flight the bubble shows status_text, the
// "result" phase clears it once TOOL_STATUS_MIN_MS is up (even past the
// answer), the pieces accumulate, and once the exchange is over the
// persisted tool_calls record (read from the history, same as a reload)
// renders through toolTraceLine.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toolTraceLine } from '../src/toolTraceLine.js'
import { TOOL_STATUS_MIN_MS } from '../src/toolStatusHold.js'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
  projectFileContentUrl: vi.fn(() => '/skin.css'),
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

describe('a live exchange shows the tool status then the persisted trace, end to end', () => {
  let chatStore
  let busChannel
  let api
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    ;({ busChannel } = await import('../src/busChannel.js'))
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    busChannel.connect()
    sockets[0].open()
  })

  afterEach(() => {
    busChannel.disconnect()
    vi.clearAllMocks()
  })

  it('publishes status_text, clears it, accumulates the pieces, then shows the persisted trace', async () => {
    chatStore.currentSessionId.value = 1
    api.getHistory.mockResolvedValue([
      { id: 51, role: 'assistant', content: 'Your flight is on time.', timestamp: 't', tool_calls: [PERSISTED_RECORD] }
    ])

    await chatStore.handleSend('where is my flight?')
    const socket = sockets[0]
    expect(socket.sent.some((f) => f.type === 'input.text')).toBe(true)

    socket.emit({ type: 'output.text_stream', session_id: 1, text: '' })
    socket.emit({ type: 'output.tool', session_id: 1, ...TOOL_START })
    await vi.waitFor(() => {
      const msg = chatStore.messages.value.find((m) => m.role === 'assistant')
      expect(msg?.statusText).toBe(TOOL_START.status_text)
    })

    socket.emit({ type: 'output.tool', session_id: 1, ...TOOL_RESULT })
    for (const text of ['Your ', 'flight ', 'is on time.']) {
      socket.emit({ type: 'output.text_stream', session_id: 1, text })
    }
    socket.emit({ type: 'state.buttons', session_id: 1, actions: [] })
    socket.emit({
      type: 'output.text', session_id: 1, assistant_message_id: 51,
      text: 'Your flight is on time.', timestamp: 't',
    })

    const finished = chatStore.messages.value.find((m) => m.messageId === 51)
    // The status line outlives the answer by design — see toolStatusHold.js.
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
