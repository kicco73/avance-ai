import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

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

describe('ChatView fades the scene again whenever the skin under it changes', () => {
  let chatStore
  let chatSkin
  let bus
  let container
  let animation

  function watchShellAnimation() {
    const shell = container.querySelector('.chat-window-shell')
    animation = { currentTime: 250, plays: 0, play() { this.plays++ } }
    shell.getAnimations = () => [animation]
    return shell
  }

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    chatSkin = await import('../src/chatSkin.js')
    global.fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '.chat-body { color: red; }' })
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
    container.remove()
  })

  async function mountedChat() {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages('proj')
    bus.deliverEntered({
      sessionId: 1, projectId: 'proj',
      state: { key: 'start', ui_label: 'Start', actions: [], chat_enabled: true },
    })
    await nextTick()
    return app
  }

  async function settled() {
    await new Promise((resolve) => setTimeout(resolve, 0))
    await new Promise((resolve) => setTimeout(resolve, 0))
    return animation.plays
  }

  it('replays the fade when the state class swaps, so the new backdrop is not snapped in', async () => {
    const app = await mountedChat()
    const shell = watchShellAnimation()
    const before = await settled()

    bus.deliverEntered({
      sessionId: 1, projectId: 'proj',
      state: { key: 'stairs', ui_label: 'Stairs', actions: [], chat_enabled: true },
    })
    await vi.waitFor(() => expect(shell.className).toContain('state-stairs'))
    await vi.waitFor(() => expect(animation.plays).toBe(before + 1))

    expect(animation.currentTime).toBe(0)

    app.unmount()
  })

  it('replays the fade when a live skin lands after the chat is already on screen', async () => {
    const app = await mountedChat()
    watchShellAnimation()
    const before = await settled()

    chatSkin.invalidateSkin()
    await vi.waitFor(() => expect(animation.plays).toBe(before + 1))

    expect(animation.currentTime).toBe(0)

    app.unmount()
  })
})
