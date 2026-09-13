// Regression: the streaming bubble used never to be reconciled against
// the message that was actually persisted, so a piece dropped mid-stream
// (a reload replacing `messages` mid-turn, or any other gap) left it
// permanently short. The whole message — `output.text`, the one that
// ends the exchange (see backend docs/BUS.md) — now replaces (never
// concatenates) the bubble's content/audioText/timestamp, re-creates the
// bubble if it was removed from `messages` in the meantime, and never
// drops it once a piece has landed even if the exchange later fails.
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

describe('the answer reconciles the streaming bubble it was being written into', () => {
  let chatStore
  let deliver

  beforeEach(async () => {
    // After resetModules the store gets a fresh copy of the fake socket —
    // the test has to publish into that one, not the first.
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('replaces content/timestamp from the answer, and keeps the spoken text', async () => {
    chatStore.currentSessionId.value = 1
    await chatStore.handleSend('hi')
    deliver({ type: 'output.text_stream', session_id: 1, text: 'Hel' })
    deliver({ type: 'output.speech', session_id: 1, text: 'audio-77' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({
      type: 'output.text', session_id: 1, assistant_message_id: 77,
      text: 'Hello, full answer.', timestamp: '2026-01-01T00:00:00Z',
    })

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant.content).toBe('Hello, full answer.')
    expect(assistant.audioText).toBe('audio-77')
    expect(assistant.timestamp).toBe('2026-01-01T00:00:00Z')
    expect(assistant.messageId).toBe(77)
  })

  it('re-creates the bubble from the answer if it was removed from `messages` mid-turn', async () => {
    chatStore.currentSessionId.value = 1
    await chatStore.handleSend('hi again')
    // A reload (or anything else) wipes the in-flight placeholder out of
    // `messages` before the answer lands.
    chatStore.messages.value = chatStore.messages.value.filter((m) => m.role !== 'assistant')
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({
      type: 'output.text', session_id: 1, assistant_message_id: 88,
      text: 'Recreated reply.', timestamp: '2026-01-01T00:00:01Z',
    })

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant).toBeTruthy()
    expect(assistant.content).toBe('Recreated reply.')
    expect(assistant.messageId).toBe(88)
  })

  it('keeps the bubble with whatever text streamed, marked failed, when the stream errors after a chunk', async () => {
    chatStore.currentSessionId.value = 1
    await chatStore.handleSend('hi')
    deliver({ type: 'output.text_stream', session_id: 1, text: 'Partial' })
    deliver({ type: 'output.error', session_id: 1, message: 'stream broke', detail: '' })

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant).toBeTruthy()
    expect(assistant.content).toBe('Partial')
    expect(assistant.failed).toBe(true)
  })

  it('drops the bubble on a stream error before any chunk arrived', async () => {
    chatStore.currentSessionId.value = 1
    await chatStore.handleSend('hi')
    deliver({ type: 'output.error', session_id: 1, message: 'stream broke immediately', detail: '' })

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant).toBeUndefined()
  })
})
