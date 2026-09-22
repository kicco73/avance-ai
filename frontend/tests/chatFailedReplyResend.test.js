import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
}))
vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))

describe('a reply the server could not write', () => {
  let chatStore
  let bus

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  function userBubbles() {
    return chatStore.messages.value.filter((m) => m.role === 'user')
  }

  function sentTexts(from = 0) {
    return bus.busChannel.send.mock.calls.slice(from).map(([frame]) => frame).filter((f) => f.type === 'input.text')
  }

  it('marks the message it was answering for resend, and resend sends it again', async () => {
    await chatStore.handleSend('hello')
    bus.deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    bus.deliver({ type: 'output.error', session_id: 1, message: 'Unexpected server error.', detail: 'boom' })

    expect(userBubbles().map((m) => [m.content, m.failed])).toEqual([['hello', true]])
    expect(chatStore.messages.value.filter((m) => m.role === 'assistant')).toHaveLength(0)

    const before = bus.busChannel.send.mock.calls.length
    await chatStore.handleResend(chatStore.messages.value.indexOf(userBubbles()[0]))

    expect(sentTexts(before)).toEqual([{ type: 'input.text', session_id: 1, text: 'hello' }])
    expect(userBubbles().map((m) => [m.content, m.failed])).toEqual([['hello', false]])
  })

  it('leaves a message that was already answered alone', async () => {
    await chatStore.handleSend('one')
    bus.deliverExchange({ sessionId: 1, chunks: ['Fine.'], answer: { id: 7, content: 'Fine.' } })
    await chatStore.handleSend('two')
    bus.deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    bus.deliver({ type: 'output.error', session_id: 1, message: 'Unexpected server error.', detail: 'boom' })

    expect(userBubbles().map((m) => [m.content, m.failed])).toEqual([['one', false], ['two', true]])
  })
})
