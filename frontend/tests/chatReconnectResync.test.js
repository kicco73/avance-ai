import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

const STATE = { key: 'a', ui_label: 'A', actions: [], chat_enabled: true }

describe('an exchange interrupted by a dropped socket', () => {
  let chatStore
  let busChannel
  let api
  let sockets

  function enter(socket, messages) {
    socket.emit({
      type: 'session.info', session_id: 1, project_id: 'proj', state: STATE,
      services: {}, audio: false, current: true, channel: 'webchat',
    })
    socket.emit({ type: 'session.messages', session_id: 1, messages })
    socket.emit({ type: 'state.buttons', session_id: 1, actions: [] })
  }

  function entered(socket) {
    return socket.sent.filter((f) => f.type === 'session.enter')
  }

  beforeEach(async () => {
    vi.useFakeTimers()
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    ;({ busChannel } = await import('../src/busChannel.js'))
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    busChannel.connect()
    sockets[0].open()
    await chatStore.loadMessages('proj')
    enter(sockets[0], [])
  })

  afterEach(() => {
    busChannel.disconnect()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('enters the conversation again on the new socket, and shows what actually landed', async () => {
    await chatStore.handleSend('where is my flight?')
    expect(sockets[0].sent.some((f) => f.type === 'input.text')).toBe(true)
    sockets[0].emit({ type: 'output.text_stream', session_id: 1, text: '' })
    sockets[0].emit({ type: 'output.text_stream', session_id: 1, text: 'On ti' })

    sockets[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    sockets[1].open()

    expect(entered(sockets[1])).toHaveLength(1)

    enter(sockets[1], [
      { id: 10, role: 'user', content: 'where is my flight?', timestamp: 't1' },
      { id: 11, role: 'assistant', content: 'On time.', timestamp: 't2' },
    ])

    const rendered = chatStore.messages.value.map((m) => [m.role, m.content])
    expect(rendered).toEqual([['user', 'where is my flight?'], ['assistant', 'On time.']])
    expect(chatStore.messages.value.every((m) => !m.failed)).toBe(true)
    expect(chatStore.chatLoading.value).toBe(false)
  })

  it('drops a message the conversation never received, rather than leaving a ghost of it on screen', async () => {
    await chatStore.handleSend('never arrived')

    sockets[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    sockets[1].open()
    enter(sockets[1], [])

    expect(chatStore.messages.value).toEqual([])
  })

  it('fails the bubble right away when the socket is not connected at all', async () => {
    busChannel.disconnect()

    await chatStore.handleSend('offline')

    const user = chatStore.messages.value.find((m) => m.content === 'offline')
    expect(user.failed).toBe(true)
    expect(chatStore.messages.value.some((m) => m.role === 'assistant')).toBe(false)
  })
})
