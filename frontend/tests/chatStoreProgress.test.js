import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn().mockResolvedValue([]),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
}))

function assistantBubble(chatStore) {
  return chatStore.messages.value.find((m) => m.role === 'assistant')
}

describe('output.progress bubble state', () => {
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

  it('has no progress on a fresh bubble', async () => {
    await chatStore.handleSend('upload my report')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })

    expect(assistantBubble(chatStore).progressPercentage).toBe(null)
  })

  it('shows the title and percentage once output.progress arrives, and un-hides a still-pending bubble', async () => {
    await chatStore.handleSend('upload my report')

    deliver({ type: 'output.progress', session_id: 1, title: 'Uploading', percentage: 42 })

    const bubble = assistantBubble(chatStore)
    expect(bubble.progressTitle).toBe('Uploading')
    expect(bubble.progressPercentage).toBe(42)
    expect(bubble.pending).toBe(false)
  })

  it('clears the bar once the reply lands, even at less than 100%', async () => {
    await chatStore.handleSend('upload my report')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'output.progress', session_id: 1, title: 'Uploading', percentage: 42 })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 51, text: 'Done.', timestamp: 't' })

    expect(assistantBubble(chatStore).progressPercentage).toBe(null)
  })

  it('ignores a progress frame for another session', async () => {
    await chatStore.handleSend('upload my report')
    chatStore.currentSessionId.value = 2

    deliver({ type: 'output.progress', session_id: 1, title: 'Uploading', percentage: 42 })

    expect(assistantBubble(chatStore)).toBeUndefined()
  })
})
