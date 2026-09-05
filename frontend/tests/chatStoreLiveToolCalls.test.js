// Regression: a live SSE turn that made a tool call used to leave the
// assistant bubble's own toolCalls unset — MessageBubble.vue's permanent
// trace (see toStoreMessage) only ever appeared after a manual reload,
// since a live turn only ever streams chunk/status_text, never the
// persisted trace itself. submitMessage now backfills it, once, from the
// same getMessages source a reload uses — see chatStoreFactory.js's own
// hadToolCall/result.assistant_message_id branch.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))

describe('a live turn backfills its own persisted tool-call trace once it lands', () => {
  let chatStore
  let chatClient
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('fetches and attaches tool_calls after a turn that fired a tool_call status', async () => {
    chatStore.currentSessionId.value = 1
    chatClient.sendMessage.mockImplementation(async (_text, _sessionId, { onStatus }) => {
      onStatus('Searching Flights…')
      onStatus('')
      return {
        reply: [], user_message_id: 40, assistant_message_id: 51,
        state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1,
      }
    })
    api.getMessages.mockResolvedValue([
      { id: 51, role: 'assistant', content: 'Found it.', timestamp: 't', tool_calls: [{ name: 'source_flights_select', summary_text: 'Searched Flights for "VY3003" · 1 row' }] },
    ])

    await chatStore.handleSend('where is my flight?')

    await vi.waitFor(() => {
      const msg = chatStore.messages.value.find((m) => m.messageId === 51)
      expect(msg?.toolCalls).toEqual([{ name: 'source_flights_select', summary_text: 'Searched Flights for "VY3003" · 1 row' }])
    })
  })

  it('never calls getMessages when no tool call happened this turn', async () => {
    chatStore.currentSessionId.value = 1
    chatClient.sendMessage.mockResolvedValue({
      reply: [], user_message_id: 40, assistant_message_id: 52,
      state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1,
    })

    await chatStore.handleSend('hi')

    expect(api.getMessages).not.toHaveBeenCalled()
  })
})
