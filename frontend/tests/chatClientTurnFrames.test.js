// chatClient.js's own turn correlation: a user message leaves as one
// `turn` frame on the single chat socket, and every frame that comes back
// is routed by the turn_id that frame minted — the only correlation there
// is. Two turns in flight at once must never see each other's chunks.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket, turnIdOf } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

describe('sendMessage over the chat websocket', () => {
  let chatClient
  let api
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    chatClient.connect()
    sockets[0].open()
  })

  afterEach(() => {
    chatClient.disconnect()
    vi.clearAllMocks()
  })

  it('sends one turn frame carrying the session and text, and resolves on the done frame with that turn_id', async () => {
    const pending = chatClient.sendMessage('where is my flight?', 7)

    const frame = sockets[0].sent[0]
    expect(frame.type).toBe('turn')
    expect(frame.session_id).toBe(7)
    expect(frame.text).toBe('where is my flight?')

    sockets[0].emit({
      type: 'done', turn_id: frame.turn_id, reply: [], user_message_id: 42, user_message_reaction: 'listening',
      assistant_message_id: 5, state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 7,
    })

    const result = await pending
    expect(result.user_message_id).toBe(42)
    // The one whitelist every field must survive (see normalizeResult).
    expect(result.user_message_reaction).toBe('listening')
    expect(result.assistant_message_id).toBe(5)
    expect(result.session_id).toBe(7)
  })

  it('never leaks one turn’s chunks or status into the other’s callbacks', async () => {
    const first = { chunks: [], statuses: [] }
    const second = { chunks: [], statuses: [] }
    const firstPending = chatClient.sendMessage('one', 1, {
      onChunk: (c) => first.chunks.push(c), onStatus: (s) => first.statuses.push(s),
    })
    const secondPending = chatClient.sendMessage('two', 1, {
      onChunk: (c) => second.chunks.push(c), onStatus: (s) => second.statuses.push(s),
    })
    const firstId = turnIdOf(sockets[0], 0)
    const secondId = turnIdOf(sockets[0], 1)
    expect(firstId).not.toBe(secondId)

    sockets[0].emit({ type: 'tool', turn_id: secondId, phase: 'start', status_text: 'Searching Flights…' })
    sockets[0].emit({ type: 'chunk', turn_id: firstId, content: 'A' })
    sockets[0].emit({ type: 'chunk', turn_id: secondId, content: 'B' })
    sockets[0].emit({ type: 'done', turn_id: secondId, reply: [], session_id: 1 })
    sockets[0].emit({ type: 'done', turn_id: firstId, reply: [], session_id: 1 })

    await Promise.all([firstPending, secondPending])
    expect(first.chunks).toEqual(['A'])
    expect(second.chunks).toEqual(['B'])
    expect(first.statuses).toEqual([])
    expect(second.statuses).toEqual(['Searching Flights…'])
  })

  it('rejects with the error frame’s own code, for that turn only', async () => {
    const pending = chatClient.sendMessage('hi', 1)
    const turnId = turnIdOf(sockets[0])

    sockets[0].emit({
      type: 'error', turn_id: turnId, message: 'Session is closed.', detail: null, code: 'session_closed',
    })

    await expect(pending).rejects.toMatchObject({ code: 'session_closed', message: 'Session is closed.' })
  })

  it('rejects immediately with chat_offline when the socket is not open — there is no HTTP fallback', async () => {
    chatClient.disconnect()

    await expect(chatClient.sendMessage('hi', 1)).rejects.toMatchObject({ code: 'chat_offline' })
  })
})
