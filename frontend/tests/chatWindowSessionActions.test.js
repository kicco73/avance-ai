// Regression coverage for removing Live Chat's own "Session" menu (the ☰
// toggle + sliding sessions panel, see the now-deleted
// chatWindowSessionsAutoCollapse.test.js) in favor of the applications
// menu's own two new top rows — New session (ProjectsMenu.vue's own
// sessionActions prop) and Close session (chatStoreFactory.js's own
// handleCloseSession). Mounts the real ChatView.vue end to end, not just
// the store refs (see chatStoreSessionIsolation.test.js for that).
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

function sentOfType(type) {
  return busChannel.send.mock.calls.map(([frame]) => frame).filter((frame) => frame.type === type)
}

function projectsPanelButtons(container) {
  return Array.from(container.querySelectorAll('.projects-panel button'))
}

function findButton(container, label) {
  return projectsPanelButtons(container).find((b) => b.textContent.trim() === label)
}

// Mounting ChatView is the heaviest thing this suite does, and the whole
// component tree is transformed here, at import time, rather than inside
// whichever test imports it first: that cost is 6.3s on its own and
// 16.3s with the whole suite running in parallel, and vitest charged it
// to that test's own 5s budget. A file's own imports are not timed, so
// the import below is left with nothing but the re-evaluation.
await import('../src/components/chat/ChatView.vue')

describe('ChatView.vue: the applications menu carries New/Close session, with no separate Session menu', () => {
  let chatStore
  let api
  let container

  beforeEach(async () => {
    resetFakeBus()
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
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages('proj')
    deliverEntered({ sessionId: 1, projectId: 'proj', state: { key: 'x', ui_label: 'X', actions: [] } })
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

  it('clicking "New session" confirms, then asks for a new one', async () => {
    const app = await mountLiveChat()
    const dialogStore = await import('../src/dialogStore.js')

    container.querySelector('.projects-btn').click()
    await nextTick()
    findButton(container, 'New session').click()
    await vi.waitFor(() => expect(sentOfType('session.create')).toHaveLength(1))

    expect(sentOfType('session.create')[0]).toMatchObject({ project_id: 'proj', session_type: 'live' })
    expect(dialogStore.confirmDialog).toHaveBeenCalled()

    app.unmount()
  })

  it('"Close session" starts enabled, closes the session with no confirmation prompt, then disables itself', async () => {
    const app = await mountLiveChat()
    const dialogStore = await import('../src/dialogStore.js')

    container.querySelector('.projects-btn').click()
    await nextTick()
    expect(findButton(container, 'Close session').disabled).toBe(false)

    findButton(container, 'Close session').click()
    await vi.waitFor(() => expect(sentOfType('session.terminate')).toHaveLength(1))
    expect(sentOfType('session.terminate')[0]).toMatchObject({ session_id: 1 })
    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()

    // Closed, said by the one place every closure passes through.
    deliver({ type: 'session.ended', session_id: 1, reason: 'user' })

    await nextTick()
    container.querySelector('.projects-btn').click() // the panel closed itself on that click — reopen it
    await nextTick()
    expect(findButton(container, 'Close session').disabled).toBe(true)

    app.unmount()
  })
})
