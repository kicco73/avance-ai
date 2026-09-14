import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { busChannel, deliver, deliverEntered, resetFakeBus } from './fakeBus.js'

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

describe('ChatView.vue: a conversation the server ended at a final state takes no more input', () => {
  let chatStore
  let container

  beforeEach(async () => {
    resetFakeBus()
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  async function mountedInFinalState() {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages('proj')
    deliverEntered({
      sessionId: 1, projectId: 'proj',
      state: { key: 'a', ui_label: 'A', chat_enabled: true, actions: [{ name: 'finish', target: 'end' }] },
      actions: [{ name: 'finish', ui_label: 'Finish' }]
    })
    await vi.waitFor(() => expect(container.querySelector('.input-row input')).not.toBeNull())
    deliver({ type: 'state.changed', session_id: 1, state: { key: 'end', ui_label: 'End', chat_enabled: true, final: true, actions: [] } })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, text: 'Bye.', assistant_message_id: 9 })
    await nextTick()
    return app
  }

  it('before session.ended the composer is still open, after it the notice says the conversation has ended and nothing takes input', async () => {
    const app = await mountedInFinalState()

    expect(container.querySelector('.input-row input').disabled).toBe(false)
    expect(container.querySelector('.chat-ended-notice')).toBeNull()

    deliver({ type: 'session.ended', session_id: 1, project_id: 'proj', reason: 'final-state' })
    await nextTick()

    expect(chatStore.selectedSessionActive.value).toBe(false)
    expect(container.querySelector('.chat-ended-notice').textContent.trim()).toBe('This conversation has ended.')
    expect(container.querySelector('.input-row input').disabled).toBe(true)
    expect(container.querySelector('.action-buttons')).toBeNull()
    chatStore.draft.value = 'one more'
    container.querySelector('.input-row').dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    expect(busChannel.send.mock.calls.map(([frame]) => frame).filter((frame) => frame.type === 'input.text')).toEqual([])

    app.unmount()
  })

  it('a session ended for any other reason keeps the generic notice', async () => {
    const app = await mountedInFinalState()

    deliver({ type: 'session.ended', session_id: 1, project_id: 'proj', reason: 'channel-switch' })
    await nextTick()

    expect(container.querySelector('.chat-ended-notice').textContent.trim()).toBe('This session is no longer active.')

    app.unmount()
  })
})
