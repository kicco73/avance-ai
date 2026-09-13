// Regression: ChatView.vue's own v-for used to key a message bubble by
// `msg.messageId || msg.id || i` — a bubble being written into starts with
// messageId: null (key falls back to msg.id), then chatStoreFactory.js
// backfills the real backend messageId once the message lands, flipping
// the v-for key and forcing Vue to unmount/remount the whole MessageBubble
// at exactly the moment its final content (and, for a tool-call exchange,
// its trace) land — the visible "glitch" a streamed reply had that a
// reload (whose messages arrive with messageId already stable) never did.
// The fix keys on msg.id first, which never changes across a message's own
// lifetime, so the bubble instance survives the messageId backfill intact.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue([]),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  deleteSession: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postTruncateSession: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'proj', ui_label: 'Proj' }], active: 'proj' }),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))

// Mounting ChatView is the heaviest thing this suite does, and the whole
// component tree is transformed here, at import time, rather than inside
// whichever test imports it first: that cost is 6.3s on its own and
// 16.3s with the whole suite running in parallel, and vitest charged it
// to that test's own 5s budget. A file's own imports are not timed, so
// the import below is left with nothing but the re-evaluation.
await import('../src/components/chat/ChatView.vue')

describe("a bubble being written into survives the messageId backfill without remounting", () => {
  let bus
  let chatStore
  let container

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  it('keeps the same .bubble-assistant DOM node from the first piece through the message landing', async () => {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages('proj')
    bus.deliverEntered({ sessionId: 1, projectId: 'proj', state: { key: 'x', ui_label: 'X', actions: [] } })
    await vi.waitFor(() => expect(container.querySelector('.projects-btn')).not.toBeNull())

    await chatStore.handleSend('hi')
    bus.deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    bus.deliver({ type: 'output.text_stream', session_id: 1, text: 'Hello' })
    await vi.waitFor(() => expect(container.querySelector('.bubble-assistant')).not.toBeNull())

    const bubbleBefore = container.querySelector('.bubble-assistant')
    expect(bubbleBefore.textContent).toContain('Hello')

    bus.deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    bus.deliver({ type: 'output.text', session_id: 1, assistant_message_id: 99, text: 'Hello', timestamp: 't' })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const bubbleAfter = container.querySelector('.bubble-assistant')
    expect(bubbleAfter).toBe(bubbleBefore)
    expect(bubbleAfter.textContent).toContain('Hello')
    expect(chatStore.messages.value.find((m) => m.role === 'assistant').messageId).toBe(99)

    app.unmount()
  })
})
