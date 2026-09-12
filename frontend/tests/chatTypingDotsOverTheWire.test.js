// The dots, from the frame the backend really puts on the socket to the
// flag the bubble reads: the real busChannel, the real store, only the
// WebSocket itself faked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  createChatSocket: vi.fn(),
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn(), clearApiError: vi.fn() }))

describe('an empty output.text_stream off the socket turns the dots on', () => {
  let chatStore
  let busChannel
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    const api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    busChannel = (await import('../src/busChannel.js')).busChannel
    chatStore = await import('../src/chatStore.js')
    busChannel.connect()
    sockets[0].open()
  })

  afterEach(() => {
    busChannel.disconnect()
    vi.clearAllMocks()
  })

  it('shows the dots, then hands the bubble over to the first real piece', async () => {
    chatStore.currentSessionId.value = 3
    await chatStore.handleSend('hi')
    const bubble = () => chatStore.messages.value.find((m) => m.role === 'assistant')

    sockets[0].emit({ type: 'output.text_stream', session_id: 3, text: '' })
    expect(bubble()).toMatchObject({ pending: false, awaitingReply: true })

    sockets[0].emit({ type: 'output.text_stream', session_id: 3, text: 'Hel' })
    expect(bubble()).toMatchObject({ awaitingReply: false, content: 'Hel' })
  })
})
