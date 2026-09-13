// Regression: a reply must stay pinned to the session it is being
// written in, never to whatever chat happens to be on screen by the time
// it finishes. The model can be slow enough that the user switches to a
// completely different chat while a message is still being written —
// without this, that stale reply (and its own state) would silently leak
// into what is now displayed. See chatStoreFactory.js's own turnSessionId
// in watchReply.
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

const STATE_B = { key: 'b', ui_label: 'B', actions: [] }

describe('a reply stays pinned to the session it is written in, even if the user switches chats first', () => {
  let chatStore
  let deliver
  let deliverEntered

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    deliverEntered = bus.deliverEntered
    chatStore = await import('../src/chatStore.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // The user switches away to a completely different chat, which answers
  // with everything about itself (see backend docs/BUS.md's session.enter).
  async function switchToB() {
    await chatStore.selectSession({ id: 2, current: true })
    deliverEntered({
      sessionId: 2,
      projectId: 'proj',
      state: STATE_B,
      messages: [{ id: 900, role: 'assistant', content: 'B history', timestamp: 't' }],
    })
  }

  it("session A's slow reply never lands on session B once the user's switched to it", async () => {
    chatStore.currentSessionId.value = 1
    await chatStore.handleSend('hello from A')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'output.text_stream', session_id: 1, text: 'Let me ' })

    await switchToB()

    expect(chatStore.currentSessionId.value).toBe(2)
    const beforeReply = chatStore.messages.value.map((m) => m.content)

    // Session A's slow reply is finally finished.
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({
      type: 'output.text', session_id: 1, assistant_message_id: 51,
      text: 'Let me look — it is on time.', timestamp: 't',
    })

    expect(chatStore.currentSessionId.value).toBe(2)
    expect(chatStore.messages.value.map((m) => m.content)).toEqual(beforeReply)
    expect(chatStore.state.value.key).toBe('b')
  })

  it('chatLoading always clears, even for a stale reply, so a switched-away-from chat is never stuck', async () => {
    chatStore.currentSessionId.value = 1
    await chatStore.handleSend('hello from A')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    expect(chatStore.chatLoading.value).toBe(true)

    await switchToB()

    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 2, text: 'done', timestamp: 't' })

    expect(chatStore.chatLoading.value).toBe(false)
  })

  it('a whole message nobody was waiting for never pushes into a chat it is not about', async () => {
    chatStore.currentSessionId.value = 1
    await switchToB()

    deliver({
      type: 'output.text', session_id: 1, assistant_message_id: 99,
      text: 'stale reply for A', timestamp: 't',
    })

    expect(chatStore.currentSessionId.value).toBe(2)
    expect(chatStore.messages.value.some((m) => m.content === 'stale reply for A')).toBe(false)
    expect(chatStore.state.value.key).toBe('b')
  })
})
