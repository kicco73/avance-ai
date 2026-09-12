// Regression for chatClient.js's own normalizeResult: what one exchange
// produced is gathered from the messages it actually sent — the whole
// messages, the choices, the state when it moved — and a field that stops
// being gathered is silently dropped before chatStore.js ever sees it,
// exactly what happened to user_message_reaction.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeChatSocket, turnIdOf } from './fakeChatSocket.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

describe('sendMessage gathers one exchange from the messages it produced', () => {
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

  it('takes the answer from the last whole message, and what came before it as prepared', async () => {
    const pending = chatClient.sendMessage('hi', 1)
    const turnId = turnIdOf(sockets[0])

    sockets[0].emit({ type: 'output.text', stream_id: turnId, message_id: 4, text: 'Wrapping up.' })
    sockets[0].emit({ type: 'output.reaction', stream_id: turnId, message_id: 42, reaction: 'listening' })
    sockets[0].emit({
      type: 'state.changed', stream_id: turnId,
      state: { key: 'a', ui_label: 'A', actions: [] }, new_state: 'a', triggered_action: 'advance',
    })
    sockets[0].emit({ type: 'ui.buttons', stream_id: turnId, actions: [{ name: 'go', ui_button: 'Go' }] })
    // The answer is published last, and is what ends the exchange.
    sockets[0].emit({ type: 'output.text', stream_id: turnId, message_id: 5, text: 'Hello.' })

    const result = await pending
    expect(result.reply).toEqual([{ id: 5, content: 'Hello.' }])
    expect(result.prepared).toEqual([{ id: 4, content: 'Wrapping up.' }])
    expect(result.assistant_message_id).toBe(5)
    expect(result.user_message_id).toBe(42)
    expect(result.user_message_reaction).toBe('listening')
    expect(result.manual_actions).toEqual([{ name: 'go', ui_button: 'Go' }])
    expect(result.state).toEqual({ key: 'a', ui_label: 'A', actions: [] })
    expect(result.state_changed).toBe(true)
    expect(result.new_state).toBe('a')
    expect(result.triggered_action).toBe('advance')
    expect(result.session_id).toBe(1)
  })

  it('reports no state change when nothing moved, and still carries the answer', async () => {
    const pending = chatClient.sendMessage('hi', 1)
    const turnId = turnIdOf(sockets[0])

    sockets[0].emit({ type: 'ui.buttons', stream_id: turnId, actions: [] })
    sockets[0].emit({ type: 'output.text', stream_id: turnId, message_id: 5, text: 'Hello.' })

    const result = await pending
    expect(result.state_changed).toBe(false)
    expect(result.state).toBe(null)
    expect(result.reply).toEqual([{ id: 5, content: 'Hello.' }])
    expect(result.prepared).toEqual([])
  })
})
