// Regression for chatClient.js's own normalizeResult: it explicitly
// whitelists which fields survive from the backend's turn response (both
// the websocket 'done' frame and the REST fallback) into what chatStore.js
// consumes — a field added to the backend response but never added here
// gets silently dropped before chatStore.js ever sees it, exactly what
// happened to user_message_reaction.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api.js', () => ({
  createChatSocket: vi.fn(),
  postChatMessage: vi.fn()
}))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

// postChatMessage now resolves to a fetch Response streaming the turn as
// SSE (see sse_turn.py's own event: done\ndata: {...} framing) rather than
// a plain parsed JSON body — this builds a minimal Response-shaped stub
// whose one chunk is that single "done" event.
function fakeSseResponse(turnData) {
  const chunk = new TextEncoder().encode(`event: done\ndata: ${JSON.stringify(turnData)}\n\n`)
  let sent = false
  return {
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: chunk }
        }
      })
    }
  }
}

describe('sendMessage (REST fallback) normalizes the full turn response', () => {
  let chatClient
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')

    // Force the REST fallback: a socket whose connection attempt fails
    // immediately, same as sendMessage's own WebSocketUnavailableError path.
    api.createChatSocket.mockImplementation(() => {
      const ws = { onopen: null, onmessage: null, onerror: null, onclose: null }
      queueMicrotask(() => ws.onclose?.())
      return ws
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('carries user_message_reaction through from the backend response', async () => {
    api.postChatMessage.mockResolvedValue(fakeSseResponse({
      reply: [],
      user_message_id: 42,
      user_message_reaction: 'listening',
      assistant_message_id: 5,
      state: { key: 'a', ui_label: 'A', actions: [] },
      'on-enter': null,
      session_id: 1
    }))

    const result = await chatClient.sendMessage('hi', 1)

    expect(result.user_message_reaction).toBe('listening')
  })
})
