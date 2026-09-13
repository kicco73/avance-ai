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
