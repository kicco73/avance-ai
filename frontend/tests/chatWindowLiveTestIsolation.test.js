import { describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn() }))
vi.mock('../src/mic.js', () => ({ startRecording: vi.fn(), stopRecording: vi.fn() }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playMessageAudio: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
  postAction: vi.fn(),
  getAutoTracking: vi.fn(),
  postAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  messageAudioUrl: vi.fn(),
  postListenTranscribe: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  projectFileContentUrl: vi.fn((p, f, s) => `/api/projects/${p}/files/${f}/content?session_id=${s}`)
}))

function makeRoot(ChatWindow, showEditProject) {
  return defineComponent({
    setup() {
      return () => h('div', [
        h('div', { class: 'app-body' }, [
          showEditProject.value ? null : h(ChatWindow, { hideSessionsPanel: false })
        ]),
        showEditProject.value ? h(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual' }) : null
      ])
    }
  })
}

describe('ChatWindow.vue onBeforeUnmount clears the shared chatStore, keeping live and Test isolated', () => {
  it('leaving Test mode never leaves the Test conversation showing in the live ChatWindow', async () => {
    const chatStore = await import('../src/chatStore.js')
    const ChatWindow = (await import('../src/components/chat/ChatWindow.vue')).default

    chatStore.state.value = { key: 'test-state', ui_label: 'Test', actions: [] }
    chatStore.currentSessionId.value = 42
    chatStore.selectedSessionActive.value = true
    chatStore.messages.value = [
      { id: 1, role: 'assistant', content: 'TEST-MODE-LEAK-CONTENT', timestamp: new Date().toISOString() }
    ]

    const showEditProject = ref(true)
    const container = document.createElement('div')
    document.body.appendChild(container)
    const app = createApp(makeRoot(ChatWindow, showEditProject))
    app.mount(container)
    await nextTick()

    showEditProject.value = false
    await nextTick()

    expect(container.textContent).not.toContain('TEST-MODE-LEAK-CONTENT')
    expect(chatStore.messages.value).toHaveLength(0)

    app.unmount()
    container.remove()
  })

  it('entering Test mode never leaves the live conversation showing in the Test ChatWindow', async () => {
    const chatStore = await import('../src/chatStore.js')
    const ChatWindow = (await import('../src/components/chat/ChatWindow.vue')).default

    chatStore.state.value = { key: 'live-state', ui_label: 'Live', actions: [] }
    chatStore.currentSessionId.value = 7
    chatStore.selectedSessionActive.value = true
    chatStore.messages.value = [
      { id: 1, role: 'assistant', content: 'LIVE-MODE-LEAK-CONTENT', timestamp: new Date().toISOString() }
    ]

    const showEditProject = ref(false)
    const container = document.createElement('div')
    document.body.appendChild(container)
    const app = createApp(makeRoot(ChatWindow, showEditProject))
    app.mount(container)
    await nextTick()

    showEditProject.value = true
    await nextTick()

    expect(container.textContent).not.toContain('LIVE-MODE-LEAK-CONTENT')
    expect(chatStore.messages.value).toHaveLength(0)

    app.unmount()
    container.remove()
  })
})
