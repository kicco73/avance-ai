import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
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

describe('every store message carries a unique local id, loaded or placeholder', () => {
  let chatStore
  let deliverEntered

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliverEntered = bus.deliverEntered
    chatStore = await import('../src/chatStore.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('never lets a loaded message and a later placeholder share the same local id', async () => {
    await chatStore.loadMessages('proj')
    deliverEntered({
      sessionId: 1,
      projectId: 'proj',
      state: { key: 'x', ui_label: 'X', actions: [] },
      messages: [{ id: 1, role: 'assistant', content: 'loaded reply', audio_text: null, timestamp: 't1' }],
    })

    const loaded = chatStore.messages.value[0]

    const userMessage = { id: loaded.id + 1000, role: 'user', content: 'placeholder', failed: false, timestamp: 't2' }
    chatStore.messages.value.push(userMessage)

    const ids = chatStore.messages.value.map((m) => m.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(loaded.messageId).toBe(1)
  })
})
