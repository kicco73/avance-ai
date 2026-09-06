// The chat socket is the only transport there is, so it has to come back
// on its own: chatClient.js reconnects with an exponential backoff that
// resets on every successful open, retries immediately when the network
// or the tab comes back, and treats an unanswered ping as a dead socket
// rather than waiting for TCP to notice.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

const PING_INTERVAL_MS = 25000
const PONG_TIMEOUT_MS = 10000

describe('chat socket reconnection', () => {
  let chatClient
  let api
  let sockets

  beforeEach(async () => {
    vi.useFakeTimers()
    vi.resetModules()
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
  })

  afterEach(() => {
    chatClient.disconnect()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('reopens after a close with a growing backoff, and resets that backoff once a connection succeeds', async () => {
    chatClient.connect()
    sockets[0].open()
    expect(chatClient.getConnectionState()).toBe('open')

    sockets[0].close()
    expect(chatClient.getConnectionState()).toBe('closed')

    // First retry after 1s.
    await vi.advanceTimersByTimeAsync(999)
    expect(sockets).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(sockets).toHaveLength(2)

    // That attempt fails: the next one waits twice as long.
    sockets[1].failToOpen()
    await vi.advanceTimersByTimeAsync(1999)
    expect(sockets).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(sockets).toHaveLength(3)

    // A success resets the backoff — the next drop retries after 1s again.
    sockets[2].open()
    expect(chatClient.getConnectionState()).toBe('open')
    sockets[2].close()
    await vi.advanceTimersByTimeAsync(1000)
    expect(sockets).toHaveLength(4)
  })

  it('retries at once when the network comes back, without waiting out the backoff', async () => {
    chatClient.connect()
    sockets[0].open()
    sockets[0].close()

    window.dispatchEvent(new Event('online'))

    expect(sockets).toHaveLength(2)
  })

  it('retries at once when the tab becomes visible again', async () => {
    chatClient.connect()
    sockets[0].open()
    sockets[0].close()

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))

    expect(sockets).toHaveLength(2)
  })

  it('pings on an interval and, with no pong back, closes the dead socket and opens a new one', async () => {
    chatClient.connect()
    sockets[0].open()

    await vi.advanceTimersByTimeAsync(PING_INTERVAL_MS)
    expect(sockets[0].sent).toEqual([{ type: 'ping' }])
    expect(sockets[0].closeCalls).toBe(0)

    await vi.advanceTimersByTimeAsync(PONG_TIMEOUT_MS)
    expect(sockets[0].closeCalls).toBe(1)
    expect(chatClient.getConnectionState()).toBe('closed')

    await vi.advanceTimersByTimeAsync(1000)
    expect(sockets).toHaveLength(2)
  })

  it('keeps the socket when the pong does come back', async () => {
    chatClient.connect()
    sockets[0].open()

    await vi.advanceTimersByTimeAsync(PING_INTERVAL_MS)
    sockets[0].emit({ type: 'pong' })
    await vi.advanceTimersByTimeAsync(PONG_TIMEOUT_MS)

    expect(sockets[0].closeCalls).toBe(0)
    expect(chatClient.getConnectionState()).toBe('open')
  })

  it('reports a reconnection to its subscribers, so the store knows to resynchronize', async () => {
    const states = []
    chatClient.onConnectionState((state, { reconnected }) => states.push([state, reconnected]))

    chatClient.connect()
    sockets[0].open()
    sockets[0].close()
    await vi.advanceTimersByTimeAsync(1000)
    sockets[1].open()

    expect(states).toEqual([
      ['connecting', false], ['open', false], ['closed', false], ['connecting', false], ['open', true],
    ])
  })
})
