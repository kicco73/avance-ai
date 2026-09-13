import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/audio.js', () => ({
  playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn(),
}))
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
  projectFileContentUrl: vi.fn(() => '/skin.css'),
}))

await import('../src/components/chat/ChatView.vue')

describe('ChatView keeps the input open while a reply is being generated', () => {
  let chatStore
  let bus
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  it('leaves the text input enabled with a turn in flight, and takes a second message', async () => {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages('proj')
    bus.deliverEntered({
      sessionId: 1, projectId: 'proj',
      state: { key: 'x', ui_label: 'X', actions: [], chat_enabled: true },
    })

    await chatStore.handleSend('I have a problem')
    bus.deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    expect(chatStore.chatLoading.value).toBe(true)

    const input = container.querySelector('textarea, input[type="text"]')
    expect(input).not.toBeNull()
    expect(input.disabled).toBe(false)

    await chatStore.handleSend('with flight VY3003')
    expect(chatStore.messages.value.filter((m) => m.role === 'user')).toHaveLength(2)
    expect(bus.busChannel.send.mock.calls.filter(([frame]) => frame.type === 'input.text')).toHaveLength(2)

    app.unmount()
  })
})
