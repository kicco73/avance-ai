// One rule, one case: an empty `output.text_stream` says the system has
// started writing, and that is what puts a bubble on screen with the
// dots in it — whether the person just asked for it, whether the
// conversation opened itself, or whether a state spoke on its own.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))

describe('an empty output.text_stream is what shows the dots', () => {
  let chatStore
  let deliver

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  function assistantBubbles() {
    return chatStore.messages.value.filter((m) => m.role === 'assistant')
  }

  it('opens no bubble until the system says it has started writing', async () => {
    await chatStore.handleSend('hi')

    expect(assistantBubbles()).toHaveLength(0)

    deliver({ type: 'output.text_stream', session_id: 1, text: '' })

    expect(assistantBubbles()).toHaveLength(1)
    expect(assistantBubbles()[0]).toMatchObject({ pending: false, awaitingReply: true })
  })

  it('opens one just the same when nobody asked — a conversation opening itself', () => {
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })

    expect(assistantBubbles()).toHaveLength(1)
    expect(assistantBubbles()[0]).toMatchObject({ pending: false, awaitingReply: true })
  })

  it('stops waiting as soon as a piece carries real text', async () => {
    await chatStore.handleSend('hi')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'output.text_stream', session_id: 1, text: 'Hel' })

    expect(assistantBubbles()[0]).toMatchObject({ awaitingReply: false, content: 'Hel' })
  })

  it('streams into that one bubble and finishes it, never opening a second', () => {
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'output.text_stream', session_id: 1, text: 'Wel' })
    deliver({ type: 'ui.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 4, text: 'Welcome.' })

    expect(assistantBubbles()).toHaveLength(1)
    expect(assistantBubbles()[0]).toMatchObject({ content: 'Welcome.', messageId: 4, awaitingReply: false })
  })

  it('leaves another chat alone', async () => {
    await chatStore.handleSend('hi')
    deliver({ type: 'output.text_stream', session_id: 2, text: '' })

    expect(assistantBubbles()).toHaveLength(0)
  })

  it('answers several sends with the one message the system wrote', async () => {
    await chatStore.handleSend('one')
    await chatStore.handleSend('two')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'ui.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 9, text: 'Both noted.' })

    expect(chatStore.messages.value.map((m) => [m.role, m.content])).toEqual([
      ['user', 'one'], ['user', 'two'], ['assistant', 'Both noted.'],
    ])
  })
})

describe('what the conversation can reach', () => {
  let chatStore
  let deliver
  let talk
  let listen

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    talk = await import('../src/skills/talk/availability.js')
    listen = await import('../src/skills/listen/availability.js')
    chatStore.currentSessionId.value = 1
  })

  it('is the session\'s own answer, not a switch read once at boot', () => {
    talk.stateListener.stateReceived({ talk_enabled: true })
    expect(talk.configured.value).toBe(true)

    deliver({ type: 'ui.services', session_id: 1, services: { talk: false, listen: true } })

    expect(talk.configured.value).toBe(false)
    expect(listen.configured.value).toBe(true)
  })

  it('says nothing about a conversation that is not the one on screen', () => {
    talk.configured.value = true

    deliver({ type: 'ui.services', session_id: 2, services: { talk: false } })

    expect(talk.configured.value).toBe(true)
  })
})
