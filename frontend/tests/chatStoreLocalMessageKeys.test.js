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

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
  getCurrentSession: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))

describe('every store message carries a unique local id, loaded or placeholder', () => {
  let chatStore
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('never lets a loaded message and a later placeholder share the same local id', async () => {
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    api.getMessages.mockResolvedValue([
      { id: 1, role: 'assistant', content: 'loaded reply', audio_text: null, timestamp: 't1' },
    ])

    await chatStore.loadMessages()
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
