// Regression for chatClient.js's own normalizeResult: it explicitly
// whitelists which fields survive from the backend's turn response (the
// websocket `done` frame) into what chatStore.js consumes — a field added
// to the backend response but never added here gets silently dropped
// before chatStore.js ever sees it, exactly what happened to
// user_message_reaction.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket, turnIdOf } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

describe('sendMessage normalizes the full turn response', () => {
  let chatClient
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    chatClient = await import('../src/chatClient.js')
    sockets = installFakeChatSocket(await import('../src/api.js'))
    chatClient.connect()
    sockets[0].open()
  })

  afterEach(() => {
    chatClient.disconnect()
    vi.clearAllMocks()
  })

  it('carries every whitelisted field through from the backend response', async () => {
    const pending = chatClient.sendMessage('hi', 1)

    sockets[0].emit({
      type: 'done',
      turn_id: turnIdOf(sockets[0]),
      reply: [{ id: 5, content: 'Hello.', timestamp: 't' }],
      user_message_id: 42,
      user_message_reaction: 'listening',
      assistant_message_id: 5,
      state: { key: 'a', ui_label: 'A', actions: [] },
      state_changed: true,
      new_state: 'a',
      triggered_action: 'advance',
      'on-enter': null,
      ai_model: { auto: true, current_index: 0, models: [] },
      session_id: 1,
    })

    const result = await pending
    expect(result.user_message_reaction).toBe('listening')
    expect(result.state_changed).toBe(true)
    expect(result.new_state).toBe('a')
    expect(result.triggered_action).toBe('advance')
    expect(result.ai_model).toEqual({ auto: true, current_index: 0, models: [] })
    expect(result.reply).toEqual([{ id: 5, content: 'Hello.', timestamp: 't' }])
  })
})
