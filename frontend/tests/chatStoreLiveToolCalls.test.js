// Regression: a live turn that made a tool call used to leave the
// assistant bubble's own toolCalls unset — MessageBubble.vue's permanent
// trace (see toStoreMessage) only ever appeared after a manual reload,
// since a live turn only ever publishes the pieces and the tool's own
// status, never the persisted trace itself. The store now backfills it,
// once, from the same history a reload reads — see chatStoreFactory.js's
// own loadToolTrace.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
}))

const TOOL_CALL_RECORD = {
  name: 'source_flights_select', arguments: { values: ['VY3003'] }, result: 'city\nParis\n',
  label: 'Flights', rows: 1, error: false, duration_ms: 12,
}

describe('a live turn backfills its own persisted tool-call trace once it lands', () => {
  let chatStore
  let deliver
  let api

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('fetches and attaches tool_calls after a turn that fired a tool call', async () => {
    api.getHistory.mockResolvedValue([
      { id: 51, role: 'assistant', content: 'Found it.', timestamp: 't', tool_calls: [TOOL_CALL_RECORD] },
    ])

    await chatStore.handleSend('where is my flight?')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'output.tool', session_id: 1, phase: 'start', status_text: 'Searching Flights…' })
    deliver({ type: 'output.tool', session_id: 1, phase: 'result' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 51, text: 'Found it.', timestamp: 't' })

    await vi.waitFor(() => {
      const msg = chatStore.messages.value.find((m) => m.messageId === 51)
      expect(msg?.toolCalls).toEqual([TOOL_CALL_RECORD])
    })
  })

  it('never reads the history when no tool call happened this turn', async () => {
    await chatStore.handleSend('hi')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 52, text: 'Hello.', timestamp: 't' })

    expect(api.getHistory).not.toHaveBeenCalled()
  })
})
