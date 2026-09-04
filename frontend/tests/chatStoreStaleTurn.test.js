// Regression: a turn's own AI reply must stay pinned to the session it was
// actually sent for, never to whatever chat happens to be on screen by the
// time it resolves. The AI provider can be slow enough that the user
// switches to a completely different chat before a turn comes back — without
// this, that stale reply (and even its own session_id/state) would silently
// leak into whatever's now displayed. See chatStoreFactory.js's own
// turnSessionId in submitMessage/handleAction.
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

function deferred() {
  let resolve
  const promise = new Promise((res) => { resolve = res })
  return { promise, resolve }
}

describe('a turn stays pinned to the session it was sent for, even if the user switches chats before it resolves', () => {
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

  it("handleSend: session A's slow reply never lands on session B once the user's switched to it", async () => {
    chatStore.currentSessionId.value = 1

    const turn = deferred()
    chatClient.sendMessage.mockReturnValue(turn.promise)

    const sendPromise = chatStore.handleSend('hello from A')

    // The user switches away to a completely different chat before the
    // slow reply comes back.
    api.getMessages.mockResolvedValue([{ id: 900, role: 'assistant', content: 'B history', timestamp: 't' }])
    api.getSessionState.mockResolvedValue({ key: 'b', ui_label: 'B', actions: [] })
    await chatStore.selectSession({ id: 2, active: true })

    expect(chatStore.currentSessionId.value).toBe(2)
    const beforeReply = chatStore.messages.value.map((m) => m.content)

    // Session A's slow reply finally comes back.
    turn.resolve({
      reply: [],
      user_message_id: 41,
      assistant_message_id: 51,
      state: { key: 'a-done', ui_label: 'A done', actions: [] },
      'on-enter': null,
      session_id: 1
    })
    await sendPromise

    // Nothing about session A's reply leaked into what's on screen.
    expect(chatStore.currentSessionId.value).toBe(2)
    expect(chatStore.messages.value.map((m) => m.content)).toEqual(beforeReply)
    expect(chatStore.state.value.key).toBe('b')
  })

  it('handleSend: chatLoading always clears, even for a stale turn, so a switched-away-from chat is never stuck', async () => {
    chatStore.currentSessionId.value = 1
    const turn = deferred()
    chatClient.sendMessage.mockReturnValue(turn.promise)

    const sendPromise = chatStore.handleSend('hello from A')
    api.getMessages.mockResolvedValue([])
    api.getSessionState.mockResolvedValue({ key: 'b', ui_label: 'B', actions: [] })
    await chatStore.selectSession({ id: 2, active: true })

    turn.resolve({
      reply: [], user_message_id: 1, assistant_message_id: 2,
      state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1
    })
    await sendPromise

    expect(chatStore.chatLoading.value).toBe(false)
  })

  it("handleAction: a slow action reply for session A never pushes into session B once switched", async () => {
    chatStore.currentSessionId.value = 1
    const turn = deferred()
    api.postAction.mockReturnValue(turn.promise)

    const actionPromise = chatStore.handleAction('advance')

    api.getMessages.mockResolvedValue([])
    api.getSessionState.mockResolvedValue({ key: 'b', ui_label: 'B', actions: [] })
    await chatStore.selectSession({ id: 2, active: true })

    turn.resolve({
      reply: [{ id: 99, content: 'stale reply for A', audio_text: null, timestamp: 't' }],
      state: { key: 'a-done', ui_label: 'A done', actions: [] },
      session_id: 1
    })
    await actionPromise

    expect(chatStore.currentSessionId.value).toBe(2)
    expect(chatStore.messages.value.some((m) => m.content === 'stale reply for A')).toBe(false)
    expect(chatStore.state.value.key).toBe('b')
  })
})
