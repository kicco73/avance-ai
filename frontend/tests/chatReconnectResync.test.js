// A socket that drops mid-turn takes the turn's own `done` with it, but
// not the turn: the backend finishes and persists it regardless (see
// backend chat/ws_notifications.py). On reconnection the store re-reads
// the session and settles whatever was still pending against what
// actually landed — resolving it from the reloaded messages, or failing
// the bubble so the user can send it again.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

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

const STATE = { key: 'a', ui_label: 'A', actions: [], chat: true }

describe('a turn interrupted by a dropped socket', () => {
  let chatStore
  let chatClient
  let api
  let sockets

  beforeEach(async () => {
    vi.useFakeTimers()
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    api.getSessionState.mockResolvedValue(STATE)
    chatStore.currentSessionId.value = 1
    chatClient.connect()
    sockets[0].open()
  })

  afterEach(() => {
    chatClient.disconnect()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('resolves from the reloaded history when the reply did land, with no duplicated bubble', async () => {
    api.getMessages.mockResolvedValue([
      { id: 10, role: 'user', content: 'where is my flight?', timestamp: 't1' },
      { id: 11, role: 'assistant', content: 'On time.', timestamp: 't2' },
    ])

    const sendPromise = chatStore.handleSend('where is my flight?')
    await vi.waitFor(() => expect(sockets[0].sent.some((f) => f.type === 'turn')).toBe(true))

    sockets[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    sockets[1].open()
    await sendPromise

    const rendered = chatStore.messages.value.map((m) => [m.role, m.content])
    expect(rendered).toEqual([['user', 'where is my flight?'], ['assistant', 'On time.']])
    expect(chatStore.messages.value.every((m) => !m.failed)).toBe(true)
  })

  it('fails the bubble when the user message never made it, so it can be resent', async () => {
    api.getMessages.mockResolvedValue([])

    const sendPromise = chatStore.handleSend('never arrived')
    await vi.waitFor(() => expect(sockets[0].sent.some((f) => f.type === 'turn')).toBe(true))

    sockets[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    sockets[1].open()
    await sendPromise

    const user = chatStore.messages.value.find((m) => m.role === 'user')
    expect(user.failed).toBe(true)
  })

  it('fails the bubble right away when the socket is not connected at all', async () => {
    chatClient.disconnect()

    await chatStore.handleSend('offline')

    const user = chatStore.messages.value.find((m) => m.content === 'offline')
    expect(user.failed).toBe(true)
    expect(chatStore.messages.value.some((m) => m.role === 'assistant')).toBe(false)
  })
})
