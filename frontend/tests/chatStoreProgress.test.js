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

describe('output.progress', () => {
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

  it('publishes the title and percentage, with no bubble of its own', () => {
    deliver({ type: 'output.progress', session_id: 1, title: 'Uploading', percentage: 42 })

    expect(chatStore.progress.value).toEqual({ title: 'Uploading', percentage: 42 })
    expect(assistantBubble(chatStore)).toBeUndefined()
  })

  it('leaves the reply being written untouched', async () => {
    await chatStore.handleSend('upload my report')
    deliver({ type: 'output.text_stream', session_id: 1, text: 'Wor' })
    deliver({ type: 'output.progress', session_id: 1, title: 'Uploading', percentage: 42 })
    deliver({ type: 'output.text_stream', session_id: 1, text: 'king' })

    expect(assistantBubble(chatStore)).toMatchObject({ content: 'Working', awaitingReply: false })
  })

  it('ignores a progress frame for another session', () => {
    chatStore.currentSessionId.value = 2

    deliver({ type: 'output.progress', session_id: 1, title: 'Uploading', percentage: 42 })

    expect(chatStore.progress.value).toBe(null)
  })
})
