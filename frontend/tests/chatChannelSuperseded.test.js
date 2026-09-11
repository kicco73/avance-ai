// chatChannel.js's handling of a channel taken over by another client of
// the same identity (see backend system/ws_notifications.py's own
// SWITCHED_TO_OTHER_CLIENT/SUPERSEDED_CLOSE_CODE). The newest connection
// wins there, so unlike every other close reason retrying must not happen:
// it would take the channel straight back off whoever is using it now.
// This settles into its own distinct connectionState instead of feeding
// the exponential-backoff reconnect loop.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))

describe('chatChannel: losing the channel to a newer client', () => {
  let chatChannel
  let SUPERSEDED_CLOSE_CODE
  let SWITCHED_TO_OTHER_CLIENT
  let api
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    ;({ chatChannel, SUPERSEDED_CLOSE_CODE, SWITCHED_TO_OTHER_CLIENT } = await import('../src/chatChannel.js'))
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
  })

  afterEach(() => {
    chatChannel.disconnect()
    vi.clearAllMocks()
  })

  it('settles into the superseded state on the frame alone, before the socket closes', async () => {
    const states = []
    chatChannel.onConnectionState((state) => states.push(state))
    chatChannel.connect()
    sockets[0].open()

    sockets[0].emit({ type: SWITCHED_TO_OTHER_CLIENT })

    expect(chatChannel.connectionState).toBe('superseded')
    expect(states).toContain('superseded')
  })

  it('stays superseded when the close that follows the frame arrives', async () => {
    chatChannel.connect()
    sockets[0].open()

    sockets[0].emit({ type: SWITCHED_TO_OTHER_CLIENT })
    sockets[0].closeWithCode(SUPERSEDED_CLOSE_CODE)
    // Let any (wrongly) scheduled reconnect's microtasks run.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(chatChannel.connectionState).toBe('superseded')
    expect(sockets).toHaveLength(1) // no reconnect attempt was made
  })

  it('settles the same way on the close code alone, when the frame never lands', async () => {
    chatChannel.connect()
    sockets[0].open()

    sockets[0].closeWithCode(SUPERSEDED_CLOSE_CODE)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(chatChannel.connectionState).toBe('superseded')
    expect(sockets).toHaveLength(1)
  })

  it('never reaches ordinary frame subscribers', async () => {
    const seen = []
    chatChannel.subscribe(SWITCHED_TO_OTHER_CLIENT, (frame) => seen.push(frame))
    chatChannel.connect()
    sockets[0].open()

    sockets[0].emit({ type: SWITCHED_TO_OTHER_CLIENT })

    expect(seen).toEqual([])
  })

  it('a normal close still goes through the ordinary closed/reconnect path', async () => {
    chatChannel.connect()
    sockets[0].open()

    sockets[0].closeWithCode(1006)

    expect(chatChannel.connectionState).toBe('closed')
  })
})
