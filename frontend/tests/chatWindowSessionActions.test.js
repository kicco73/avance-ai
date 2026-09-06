// Regression coverage for removing Live Chat's own "Session" menu (the ☰
// toggle + sliding sessions panel, see the now-deleted
// chatWindowSessionsAutoCollapse.test.js) in favor of the applications
// menu's own two new top rows — New session (ProjectsMenu.vue's own
// sessionActions prop) and Close session (chatStoreFactory.js's own
// handleCloseSession). Mounts the real ChatView.vue end to end, not just
// the store refs (see chatStoreSessionIsolation.test.js for that).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), getConnectionState: vi.fn(() => 'open'), onConnectionState: vi.fn(() => () => {}), resolvePendingTurnsAfterReload: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/mic.js', () => ({ startRecording: vi.fn(), stopRecording: vi.fn() }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playMessageAudio: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  postCloseSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn().mockResolvedValue([]),
  getSessionState: vi.fn(),
  postAction: vi.fn(),
  getAutoTracking: vi.fn(),
  postAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  putMessageReaction: vi.fn(),
  messageAudioUrl: vi.fn(),
  postListenTranscribe: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'proj', ui_label: 'Proj' }], active: 'proj' }),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))

function projectsPanelButtons(container) {
  return Array.from(container.querySelectorAll('.projects-panel button'))
}

function findButton(container, label) {
  return projectsPanelButtons(container).find((b) => b.textContent.trim() === label)
}

describe('ChatView.vue: the applications menu carries New/Close session, with no separate Session menu', () => {
  let chatStore
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  async function mountLiveChat() {
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages()
    await vi.waitFor(() => expect(container.querySelector('.projects-btn')).not.toBeNull())
    await vi.waitFor(() => expect(container.querySelector('.projects-btn').disabled).toBe(false))
    return app
  }

  it('there is no ☰ sessions toggle or sliding sessions panel left in the header', async () => {
    const app = await mountLiveChat()

    expect(container.querySelector('.sessions-panel-wrap')).toBeNull()
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent.trim() === '☰')).toBe(false)

    app.unmount()
  })

  it('the applications menu opens with New session / Close session above a divider, above the project list', async () => {
    const app = await mountLiveChat()

    container.querySelector('.projects-btn').click()
    await vi.waitFor(() => expect(container.querySelector('.project-entry')).not.toBeNull())

    const items = Array.from(container.querySelectorAll('.projects-panel button, .projects-panel .projects-menu-divider'))
    const labels = items.map((el) => (el.classList.contains('projects-menu-divider') ? '(divider)' : el.textContent.trim()))
    expect(labels.slice(0, 4)).toEqual(['New session', 'Close session', '(divider)', '✓Proj'])

    app.unmount()
  })

  it('marks the active project with a ✓, matched by id (not name — the backend row carries no such field)', async () => {
    api.getProjects.mockResolvedValue({
      projects: [{ id: 'proj', ui_label: 'Proj' }, { id: 'other', ui_label: 'Other' }],
      active: 'proj'
    })
    const app = await mountLiveChat()

    container.querySelector('.projects-btn').click()
    await vi.waitFor(() => expect(container.querySelectorAll('.project-entry')).toHaveLength(2))

    const [activeRow, otherRow] = projectsPanelButtons(container).filter((b) => b.classList.contains('projects-item') && !b.classList.contains('projects-session-item'))
    expect(activeRow.querySelector('.projects-item-check').textContent.trim()).toBe('✓')
    expect(otherRow.querySelector('.projects-item-check').textContent.trim()).toBe('')

    app.unmount()
  })

  it('clicking "New session" confirms, then starts a new session', async () => {
    const app = await mountLiveChat()
    const dialogStore = await import('../src/dialogStore.js')
    api.postCreateSession.mockResolvedValue({ id: 2, active: true })
    api.getCurrentSession.mockResolvedValue({ id: 2, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    container.querySelector('.projects-btn').click()
    await nextTick()
    findButton(container, 'New session').click()
    await vi.waitFor(() => expect(api.postCreateSession).toHaveBeenCalled())

    expect(dialogStore.confirmDialog).toHaveBeenCalled()

    app.unmount()
  })

  it('"Close session" starts enabled, closes the session with no confirmation prompt, then disables itself', async () => {
    const app = await mountLiveChat()
    const dialogStore = await import('../src/dialogStore.js')

    container.querySelector('.projects-btn').click()
    await nextTick()
    expect(findButton(container, 'Close session').disabled).toBe(false)

    api.postCloseSession.mockResolvedValue({ id: 1, active: false })
    findButton(container, 'Close session').click()
    await vi.waitFor(() => expect(api.postCloseSession).toHaveBeenCalledWith(1))
    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()

    await nextTick()
    container.querySelector('.projects-btn').click() // the panel closed itself on that click — reopen it
    await nextTick()
    expect(findButton(container, 'Close session').disabled).toBe(true)

    app.unmount()
  })
})
