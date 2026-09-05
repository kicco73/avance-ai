// Regression: submitMessage used to never reconcile the streaming bubble
// against the turn's own persisted assistant message — done.reply was
// always empty, so a chunk dropped mid-stream (a reload replacing
// `messages` mid-turn, or any other gap) left the bubble permanently
// short. Now that the backend populates done.reply with the persisted
// {id, content, audio_text, timestamp} row (see
// TrackingProcessor._build_turn_response), submitMessage uses it to
// replace (never concatenate) the bubble's content/audioText/timestamp,
// re-creates the bubble if it was removed from `messages` in the
// meantime, and never drops it once a chunk has landed even if the
// stream itself later errors.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))

describe('submitMessage reconciles the streaming bubble against done.reply', () => {
  let chatStore
  let chatClient

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('replaces content/audioText/timestamp from done.reply[0] instead of concatenating', async () => {
    chatStore.currentSessionId.value = 1
    chatClient.sendMessage.mockImplementation(async (_text, _sessionId, { onChunk }) => {
      onChunk('Hel')
      return {
        reply: [{ id: 77, content: 'Hello, full answer.', audio_text: 'audio-77', timestamp: '2026-01-01T00:00:00Z' }],
        user_message_id: 40, assistant_message_id: 77,
        state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1,
      }
    })

    await chatStore.handleSend('hi')

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant.content).toBe('Hello, full answer.')
    expect(assistant.audioText).toBe('audio-77')
    expect(assistant.timestamp).toBe('2026-01-01T00:00:00Z')
    expect(assistant.messageId).toBe(77)
  })

  it('re-creates the bubble from done.reply if it was removed from `messages` mid-turn', async () => {
    chatStore.currentSessionId.value = 1
    chatClient.sendMessage.mockImplementation(async () => {
      // Simulate a reload (or anything else) wiping the in-flight
      // placeholder out of `messages` before the turn resolves.
      chatStore.messages.value = chatStore.messages.value.filter((m) => m.role !== 'assistant')
      return {
        reply: [{ id: 88, content: 'Recreated reply.', audio_text: null, timestamp: '2026-01-01T00:00:01Z' }],
        user_message_id: 41, assistant_message_id: 88,
        state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1,
      }
    })

    await chatStore.handleSend('hi again')

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant).toBeTruthy()
    expect(assistant.content).toBe('Recreated reply.')
    expect(assistant.messageId).toBe(88)
  })

  it('keeps the bubble with whatever text streamed, marked failed, when the stream errors after a chunk', async () => {
    chatStore.currentSessionId.value = 1
    chatClient.sendMessage.mockImplementation(async (_text, _sessionId, { onChunk }) => {
      onChunk('Partial')
      throw new Error('stream broke')
    })

    await chatStore.handleSend('hi')

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant).toBeTruthy()
    expect(assistant.content).toBe('Partial')
    expect(assistant.failed).toBe(true)
  })

  it('drops the bubble on a stream error before any chunk arrived', async () => {
    chatStore.currentSessionId.value = 1
    chatClient.sendMessage.mockImplementation(async () => {
      throw new Error('stream broke immediately')
    })

    await chatStore.handleSend('hi')

    const assistant = chatStore.messages.value.find((m) => m.role === 'assistant')
    expect(assistant).toBeUndefined()
  })
})
