// Regression coverage for testModeProjectName (see chatStore.js's own
// docstring): EditProjectView.vue's own embedded "Test" chat must always
// route through the draft-session endpoints (getCurrentTestSession/
// postCreateTestSession/getTestSessions), never the real ones a client
// could otherwise get to look up someone else's "Test" sessions or vice
// versa — and every other caller (testModeProjectName left null) must
// keep using the real ones exactly as before.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
  postAction: vi.fn(),
  getAutoTracking: vi.fn(),
  postAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  messageAudioUrl: vi.fn(),
  postListenTranscribe: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn()
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

describe('testModeProjectName routes session bootstrap/list to the right pool', () => {
  let chatStore
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    api.getMessages.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('loadMessages calls getCurrentSession, never getCurrentTestSession, when testModeProjectName is null', async () => {
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    await chatStore.loadMessages()

    expect(api.getCurrentSession).toHaveBeenCalledWith(null)
    expect(api.getCurrentTestSession).not.toHaveBeenCalled()
  })

  it('loadMessages calls getCurrentTestSession, never getCurrentSession, when testModeProjectName is set', async () => {
    api.getCurrentTestSession.mockResolvedValue({ id: 2, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    chatStore.testModeProjectName.value = 'my-project'

    await chatStore.loadMessages()

    expect(api.getCurrentTestSession).toHaveBeenCalledWith(null, 'my-project')
    expect(api.getCurrentSession).not.toHaveBeenCalled()
  })

  it('loadSessions calls getSessions, never getTestSessions, when testModeProjectName is null', async () => {
    await chatStore.loadSessions()

    expect(api.getSessions).toHaveBeenCalled()
    expect(api.getTestSessions).not.toHaveBeenCalled()
  })

  it('loadSessions calls getTestSessions, never getSessions, when testModeProjectName is set', async () => {
    chatStore.testModeProjectName.value = 'my-project'

    await chatStore.loadSessions()

    expect(api.getTestSessions).toHaveBeenCalledWith('my-project')
    expect(api.getSessions).not.toHaveBeenCalled()
  })

  it('handleNewSession calls postCreateSession when testModeProjectName is null', async () => {
    api.postCreateSession.mockResolvedValue({ id: 3, active: true })
    api.getCurrentSession.mockResolvedValue({ id: 3, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    await chatStore.handleNewSession()

    expect(api.postCreateSession).toHaveBeenCalled()
    expect(api.postCreateTestSession).not.toHaveBeenCalled()
  })

  it('handleNewSession calls postCreateTestSession, scoped to the project, when testModeProjectName is set', async () => {
    api.postCreateTestSession.mockResolvedValue({ id: 4, active: true })
    api.getCurrentTestSession.mockResolvedValue({ id: 4, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    chatStore.testModeProjectName.value = 'my-project'

    await chatStore.handleNewSession()

    expect(api.postCreateTestSession).toHaveBeenCalledWith('my-project')
    expect(api.postCreateSession).not.toHaveBeenCalled()
  })

  it('handleNewSession runs a brand new session\'s own on-enter (init-action fired it)', async () => {
    api.postCreateSession.mockResolvedValue({ id: 5, active: true, 'on-enter': 'celebrate()' })
    api.getCurrentSession.mockResolvedValue({ id: 5, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    const onEnterActions = await import('../src/onEnterActions.js')
    await chatStore.handleNewSession()

    expect(onEnterActions.runOnEnterScript).toHaveBeenCalledWith('celebrate()')
  })

  it('handleReset runs the reset response\'s own on-enter (also entering through init-action)', async () => {
    api.postResetTestSessions.mockResolvedValue({ key: 'a', ui_label: 'A', actions: [], 'on-enter': 'celebrate()' })
    api.getCurrentSession.mockResolvedValue({ id: 6, active: true, state: { key: 'a', ui_label: 'A', actions: [] } })

    const onEnterActions = await import('../src/onEnterActions.js')
    await chatStore.handleReset()

    expect(onEnterActions.runOnEnterScript).toHaveBeenCalledWith('celebrate()')
    // The "on-enter" wire key must never leak into the displayed state object.
    expect(chatStore.state.value).toEqual({ key: 'a', ui_label: 'A', actions: [] })
  })
})
