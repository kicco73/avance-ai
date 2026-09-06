// Regression for chatClient.js's own frame dispatch: a 'tool' frame's own
// status_text must reach the caller's onStatus verbatim on phase "start",
// and any other phase must clear it (onStatus('')) — see ai_service.py's
// own tool-call loop and chatStoreFactory.js's own per-bubble statusText
// wiring this feeds.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket, turnIdOf } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

describe('sendMessage forwards the tool frame as onStatus', () => {
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

  it('calls onStatus with status_text on phase "start", then with empty string on phase "result"', async () => {
    const statuses = []
    const pending = chatClient.sendMessage('hi', 1, { onStatus: (text) => statuses.push(text) })
    const turnId = turnIdOf(sockets[0])

    sockets[0].emit({ type: 'tool', turn_id: turnId, phase: 'start', status_text: 'Searching Flights…' })
    sockets[0].emit({ type: 'tool', turn_id: turnId, phase: 'result' })
    sockets[0].emit({ type: 'done', turn_id: turnId, reply: [], session_id: 1 })

    await pending
    expect(statuses).toEqual(['Searching Flights…', ''])
  })

  it('never calls onStatus when no tool call happens this turn', async () => {
    const statuses = []
    const pending = chatClient.sendMessage('hi', 1, { onStatus: (text) => statuses.push(text) })
    const turnId = turnIdOf(sockets[0])

    sockets[0].emit({ type: 'chunk', turn_id: turnId, content: 'Hello.' })
    sockets[0].emit({ type: 'done', turn_id: turnId, reply: [], session_id: 1 })

    await pending
    expect(statuses).toEqual([])
  })
})
