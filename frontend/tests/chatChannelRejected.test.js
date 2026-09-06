// chatChannel.js's handling of ALREADY_CONNECTED_CLOSE_CODE (see backend
// chat/ws_notifications.py's own constant of the same name): this identity
// is already at its per-role connection cap on another tab. Unlike every
// other close reason, retrying can't help — the cap only frees up when the
// other connection goes away — so this must settle into its own distinct
// connectionState instead of feeding the exponential-backoff reconnect loop.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))

describe('chatChannel: the already-connected-elsewhere close code', () => {
  let chatChannel
  let ALREADY_CONNECTED_CLOSE_CODE
  let api
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    ;({ chatChannel, ALREADY_CONNECTED_CLOSE_CODE } = await import('../src/chatChannel.js'))
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
  })

  afterEach(() => {
    chatChannel.disconnect()
    vi.clearAllMocks()
  })

  it('settles into the rejected state and never retries', async () => {
    const states = []
    chatChannel.onConnectionState((state) => states.push(state))
    chatChannel.connect()
    sockets[0].open()

    sockets[0].closeWithCode(ALREADY_CONNECTED_CLOSE_CODE)
    // Let any (wrongly) scheduled reconnect's microtasks run.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(chatChannel.connectionState).toBe('rejected')
    expect(states).toContain('rejected')
    expect(sockets).toHaveLength(1) // no reconnect attempt was made
  })

  it('a normal close still goes through the ordinary closed/reconnect path', async () => {
    chatChannel.connect()
    sockets[0].open()

    sockets[0].closeWithCode(1006)

    expect(chatChannel.connectionState).toBe('closed')
  })
})
