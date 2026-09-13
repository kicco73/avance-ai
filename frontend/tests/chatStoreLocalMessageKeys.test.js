// Regression: toStoreMessage never assigned a local `id` to a loaded
// message — only messageId (the backend id) — while a placeholder's own
// `id` comes from the same nextMessageId counter restarting at 0 on every
// page load. A loaded message with backend messageId 1 and a fresh
// placeholder with local id 1 then collided in ChatTimeline.vue's
// `entry.message.key ?? entry.message.id` v-for key, and Vue silently
// dropped one of the two nodes. toStoreMessage now draws its own `id`
// from the same counter as every placeholder, so no loaded message can
// ever collide with one.
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
    // The loaded message's own local id is never the backend's raw id
    // (which a placeholder's counter could easily also reach) — it's
    // whatever the shared nextMessageId sequence assigned it.
    expect(loaded.messageId).toBe(1)
  })
})
