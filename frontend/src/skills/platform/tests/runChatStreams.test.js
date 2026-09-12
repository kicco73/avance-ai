// The editor's Run chat is the same conversation as any other, shown
// differently: it draws a timeline (messages plus transitions) instead of
// a plain list. Whatever it draws it with, a reply has to arrive the same
// way — the dots while it is being written, then the text as it is
// written — and the in-flight bubble has to reach the timeline.
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../../../api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../api.js', () => ({
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getTestSessions: vi.fn(),
  postResetTestSessions: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  postSessionAction: vi.fn(),
  getTranscript: vi.fn(async () => []),
  getSessions: vi.fn(async () => []),
  getSessionSignals: vi.fn(async () => []),
}))
vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))

describe('the Run chat', () => {
  let testStore
  let deliver

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('../../../../tests/fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    testStore = (await import('../testChatStore.js')).testStore
    testStore.currentSessionId.value = 5
  })

  function assistantBubbles() {
    return testStore.messages.value.filter((m) => m.role === 'assistant')
  }

  it('shows the dots while the reply is being written', async () => {
    await testStore.handleSend('ciao')

    deliver({ type: 'output.text_stream', session_id: 5, text: '' })

    expect(assistantBubbles()).toHaveLength(1)
    expect(assistantBubbles()[0]).toMatchObject({ pending: false, awaitingReply: true })
  })

  it('streams the text as it is written', async () => {
    await testStore.handleSend('ciao')
    deliver({ type: 'output.text_stream', session_id: 5, text: '' })
    deliver({ type: 'output.text_stream', session_id: 5, text: 'Ho' })
    deliver({ type: 'output.text_stream', session_id: 5, text: 'la' })

    expect(assistantBubbles()[0]).toMatchObject({ content: 'Hola', awaitingReply: false })
  })

  // Through the real chain the Run panel draws with, not buildTimeline
  // called by hand: the timeline is a computed over the store's own
  // messages, and a bubble that never reaches it is a bubble nobody sees.
  it('puts the bubble being written into the timeline, before it has an id', async () => {
    const { useLiveRunTimeline } = await import('../useLiveRunTimeline.js')
    const { ref } = await import('vue')
    const { timeline } = useLiveRunTimeline('proj', ref('live'), ref(new Set()))

    await testStore.handleSend('ciao')
    deliver({ type: 'output.text_stream', session_id: 5, text: '' })

    const drawn = timeline.value.filter((entry) => entry.kind === 'message').map((entry) => entry.message)
    expect(drawn.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(drawn.at(-1)).toMatchObject({ role: 'assistant', awaitingReply: true })

    deliver({ type: 'output.text_stream', session_id: 5, text: 'Hola' })
    const written = timeline.value.filter((entry) => entry.kind === 'message').map((entry) => entry.message)
    expect(written.at(-1)).toMatchObject({ content: 'Hola', awaitingReply: false })
  })
})
