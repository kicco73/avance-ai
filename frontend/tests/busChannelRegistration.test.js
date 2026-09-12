// Being connected is not being subscribed: the socket is a bus
// connection, so the channel tells the server which of the exportable
// events (SERVER_EVENTS, mirroring backend WEB_FORWARDED) anything is
// actually listening for — and stops telling it once nothing is.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))

describe('busChannel registration', () => {
  let busChannel
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    ;({ busChannel } = await import('../src/busChannel.js'))
    const api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    busChannel.connect()
    sockets[0].open()
  })

  afterEach(() => {
    busChannel.disconnect()
    vi.clearAllMocks()
  })

  function registrations(socket) {
    return socket.sent.filter((frame) => frame.type === 'subscribe' || frame.type === 'unsubscribe')
  }

  it('asks the server once for an exportable event, however many local subscribers it has', () => {
    const first = busChannel.subscribe('ui.notification', () => {})
    busChannel.subscribe('ui.notification', () => {})

    expect(registrations(sockets[0])).toEqual([{ type: 'subscribe', events: ['ui.notification'] }])

    // Still one listener left — the server must keep sending it.
    first()
    expect(registrations(sockets[0])).toEqual([{ type: 'subscribe', events: ['ui.notification'] }])
  })

  it('drops the registration when the last local subscriber goes', () => {
    const unsubscribe = busChannel.subscribe('ui.progress', () => {})
    unsubscribe()

    expect(registrations(sockets[0])).toEqual([
      { type: 'subscribe', events: ['ui.progress'] },
      { type: 'unsubscribe', events: ['ui.progress'] }
    ])
  })

  it('never registers a frame type the server does not export', () => {
    busChannel.subscribe('turn.ended', () => {})
    busChannel.subscribe('output.text_stream', () => {})

    expect(registrations(sockets[0])).toEqual([])
  })

  it('registers human_prompt, which is what makes this tab the one answering', () => {
    busChannel.subscribe('human_prompt', () => {})

    expect(registrations(sockets[0])).toEqual([{ type: 'subscribe', events: ['human_prompt'] }])
  })

  it('restates every live registration on a reconnection, and nothing about one already dropped', async () => {
    busChannel.subscribe('ui.notification', () => {})
    const dropped = busChannel.subscribe('ui.system_warning', () => {})
    dropped()

    vi.useFakeTimers()
    try {
      sockets[0].closeWithCode(1006)
      await vi.advanceTimersByTimeAsync(1000)
    } finally {
      vi.useRealTimers()
    }
    sockets[1].open()

    expect(registrations(sockets[1])).toEqual([{ type: 'subscribe', events: ['ui.notification'] }])
  })
})
