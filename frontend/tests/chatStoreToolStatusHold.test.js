// The tool status line ("Searching …") must stay readable even when the
// tool answers in a few milliseconds: the store keeps it for at least
// TOOL_STATUS_MIN_MS from the moment it was shown (see toolStatusHold.js),
// and "done" never cuts it short. A result that arrives after the minimum
// clears it at once.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TOOL_STATUS_MIN_MS } from '../src/toolStatusHold.js'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))

const DONE = {
  reply: [{ id: 51, content: 'Found it.', timestamp: 't' }], user_message_id: 40, assistant_message_id: 51,
  state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1,
}

function assistantStatus(chatStore) {
  return chatStore.messages.value.find((m) => m.role === 'assistant')?.statusText
}

describe('tool status minimum display time', () => {
  let chatStore
  let chatClient
  let api

  beforeEach(async () => {
    vi.useFakeTimers()
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
    api.getMessages.mockResolvedValue([])
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('keeps the status shown when the result lands after 10 ms, and clears it once TOOL_STATUS_MIN_MS is up', async () => {
    chatClient.sendMessage.mockImplementation(async (_text, _sessionId, { onStatus }) => {
      onStatus('Searching Flights…')
      await vi.advanceTimersByTimeAsync(10)
      onStatus('')
      return DONE
    })

    await chatStore.handleSend('where is my flight?')

    expect(assistantStatus(chatStore)).toBe('Searching Flights…')
    await vi.advanceTimersByTimeAsync(1000 - 10)
    expect(assistantStatus(chatStore)).toBe('Searching Flights…')
    await vi.advanceTimersByTimeAsync(600)
    expect(assistantStatus(chatStore)).toBe('')
  })

  it('clears the status at once when the result lands after the minimum has already passed', async () => {
    let afterResult = null
    chatClient.sendMessage.mockImplementation(async (_text, _sessionId, { onStatus }) => {
      onStatus('Searching Flights…')
      await vi.advanceTimersByTimeAsync(2000)
      onStatus('')
      afterResult = assistantStatus(chatStore)
      return DONE
    })

    await chatStore.handleSend('where is my flight?')

    expect(afterResult).toBe('')
    expect(assistantStatus(chatStore)).toBe('')
  })

  it('does not let "done" cut the minimum short', async () => {
    chatClient.sendMessage.mockImplementation(async (_text, _sessionId, { onStatus }) => {
      onStatus('Searching Flights…')
      onStatus('')
      return DONE
    })

    await chatStore.handleSend('where is my flight?')

    expect(assistantStatus(chatStore)).toBe('Searching Flights…')
    await vi.advanceTimersByTimeAsync(TOOL_STATUS_MIN_MS)
    expect(assistantStatus(chatStore)).toBe('')
  })
})
