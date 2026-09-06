// chatChannel.js's own observer contract: the channel owns the socket and
// routes every inbound frame purely by its `type`, to as many subscribers
// as asked for that type. chatClient.js is one of those subscribers, not
// the dispatcher — a notification or a system_warning reaches its consumer
// without passing through the chat at all.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))

describe('chatChannel', () => {
  let chatChannel
  let api
  let sockets

  beforeEach(async () => {
    vi.resetModules()
    ;({ chatChannel } = await import('../src/chatChannel.js'))
    api = await import('../src/api.js')
    sockets = installFakeChatSocket(api)
    chatChannel.connect()
    sockets[0].open()
  })

  afterEach(() => {
    chatChannel.disconnect()
    vi.clearAllMocks()
  })

  it('fans one frame out to every subscriber of its type, and to no other type', () => {
    const warnings = []
    const notifications = []
    chatChannel.subscribe('system_warning', (frame) => warnings.push(frame))
    chatChannel.subscribe('system_warning', (frame) => warnings.push(frame))
    chatChannel.subscribe('notification', (frame) => notifications.push(frame))

    sockets[0].emit({ type: 'system_warning', kind: 'project_broken', project_id: 'p', line: 4 })

    expect(warnings).toHaveLength(2)
    expect(warnings[0]).toEqual({ type: 'system_warning', kind: 'project_broken', project_id: 'p', line: 4 })
    expect(notifications).toEqual([])
  })

  it('stops delivering to a handler that unsubscribed, leaving the others alone', () => {
    const kept = []
    const dropped = []
    chatChannel.subscribe('test_update', (frame) => kept.push(frame))
    const unsubscribe = chatChannel.subscribe('test_update', (frame) => dropped.push(frame))

    unsubscribe()
    sockets[0].emit({ type: 'test_update', key: 'batch:state:a' })

    expect(kept).toHaveLength(1)
    expect(dropped).toEqual([])
  })

  it('ignores a frame nobody subscribed to, and never hands `pong` to subscribers', () => {
    const seen = []
    chatChannel.subscribe('pong', (frame) => seen.push(frame))

    sockets[0].emit({ type: 'nothing_listens_to_this' })
    sockets[0].emit({ type: 'pong' })

    expect(seen).toEqual([])
  })

  it('sends a frame on the open socket, and reports no socket rather than throwing', () => {
    expect(chatChannel.send({ type: 'turn', text: 'hi' })).toBe(true)
    expect(sockets[0].sent).toEqual([{ type: 'turn', text: 'hi' }])

    chatChannel.disconnect()

    expect(chatChannel.isOpen).toBe(false)
    expect(chatChannel.send({ type: 'turn', text: 'lost' })).toBe(false)
  })
})
