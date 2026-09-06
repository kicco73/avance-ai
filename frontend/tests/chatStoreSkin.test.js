// Real, executable verification of chatSkin.js's shared index.css "skin"
// loader (see its own module-level watch/loadSkin) — not just a read of
// the code. Drives the actual exported refs the way RunChat.vue/
// ChatWindow.vue really do, mocks only fetch, and asserts on the real
// jsdom document.head: does a request actually go out, does a <style>
// tag actually land, does toggling applyAspect back on actually resume
// loading, is there ever more than one tag at once.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), getConnectionState: vi.fn(() => 'open'), onConnectionState: vi.fn(() => () => {}), resolvePendingTurnsAfterReload: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn() }))
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
  postTruncateSession: vi.fn(),
  projectFileContentUrl: vi.fn((projectName, fileName, sessionId) => `/api/projects/${projectName}/files/${fileName}/content?session_id=${sessionId}`)
}))

function currentSkinStyleTags() {
  return Array.from(document.head.querySelectorAll('style'))
}

function css(color) {
  return `body { color: ${color}; }`
}

describe("chatSkin.js's shared index.css skin loader, driven by the live store", () => {
  let chatStore
  let chatSkin
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../src/chatStore.js')
    chatSkin = await import('../src/chatSkin.js')
    fetchMock = vi.fn()
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  async function loadRed() {
    fetchMock.mockResolvedValue({ ok: true, text: async () => css('red') })
    chatStore.currentProjectId.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
  }

  it('does nothing until a project+session are set, then fetches and applies the skin', async () => {
    await nextTick()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(currentSkinStyleTags()).toHaveLength(0)

    fetchMock.mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: red; }' })
    chatStore.currentProjectId.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/proj/files/index.css/content?session_id=1',
      expect.objectContaining({ credentials: 'include', cache: 'no-store' })
    )
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
    expect(currentSkinStyleTags()[0].textContent).toBe('.chat-window-shell { color: red; }')
  })

  it('a skinVersion bump re-fetches into the same tag, and a non-ok response clears it rather than leaving a stale one', async () => {
    await loadRed()

    fetchMock.mockResolvedValue({ ok: true, text: async () => css('blue') })
    chatSkin.invalidateSkin()
    await vi.waitFor(() => expect(currentSkinStyleTags()[0]?.textContent).toBe(css('blue')))
    expect(currentSkinStyleTags()).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    fetchMock.mockResolvedValue({ ok: false })
    chatSkin.invalidateSkin()
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(0))
  })

  it('applyAspect off removes the skin and short-circuits before the fetch; turning it back on resumes loading', async () => {
    await loadRed()

    chatSkin.applyAspect.value = false
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(0))
    expect(fetchMock).toHaveBeenCalledTimes(1)

    fetchMock.mockResolvedValue({ ok: true, text: async () => css('green') })
    chatSkin.applyAspect.value = true
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
    expect(currentSkinStyleTags()[0].textContent).toBe(css('green'))
  })

  it('switching project+session (e.g. leaving then re-entering Test) fetches the new one', async () => {
    await loadRed()

    fetchMock.mockResolvedValue({ ok: true, text: async () => css('purple') })
    chatStore.currentProjectId.value = 'proj-b'
    chatStore.currentSessionId.value = 2

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/projects/proj-b/files/index.css/content?session_id=2',
      expect.anything()
    )
    expect(currentSkinStyleTags()).toHaveLength(1)
    expect(currentSkinStyleTags()[0].textContent).toBe(css('purple'))
  })
})

describe('chatSkin.js only ever applies the currently active store — live vs test never fight over the one <style> tag', () => {
  let chatStore
  let testChatStore
  let chatSkin
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../src/chatStore.js')
    testChatStore = await import('../src/testChatStore.js')
    testChatStore.setTestProject('draft-project')
    chatSkin = await import('../src/chatSkin.js')
    fetchMock = vi.fn()
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  it('a project/session change on the inactive store is ignored until switching modes makes it active', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => css('red') })
    chatStore.currentProjectId.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))

    // Test mode's own store resolves in the background (e.g. RunChat.vue
    // mounted once) — must not touch the live skin while 'live' is active.
    fetchMock.mockResolvedValue({ ok: true, text: async () => css('draft') })
    testChatStore.currentProjectId.value = 'draft-project'
    testChatStore.currentSessionId.value = 99
    await nextTick()
    await nextTick()
    expect(currentSkinStyleTags()[0].textContent).toBe(css('red'))
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Switching modes (EditProjectView's setMode('run')) immediately
    // swaps to the test store's own already-resolved project/session.
    chatSkin.activeChatMode.value = 'test'
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(currentSkinStyleTags()[0].textContent).toBe(css('draft')))

    // Leaving 'run' mode swaps straight back — the live store's project/
    // session never had to be touched or reloaded to make this happen.
    fetchMock.mockResolvedValue({ ok: true, text: async () => css('red') })
    chatSkin.activeChatMode.value = 'live'
    await vi.waitFor(() => expect(currentSkinStyleTags()[0].textContent).toBe(css('red')))
  })
})

describe('the real Test-mode bootstrap sequence (loadMessages -> ensureSession) actually reaches loadSkin', () => {
  let testChatStore
  let chatSkin
  let api
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    testChatStore = await import('../src/testChatStore.js')
    testChatStore.setTestProject('ttm_prototype_2')
    chatSkin = await import('../src/chatSkin.js')
    chatSkin.activeChatMode.value = 'test'
    api = await import('../src/api.js')
    api.getMessages.mockResolvedValue([])
    fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: teal; }' })
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  it('entering Test mode (loadMessages called on the test store) fetches and applies the skin, using the exact real session payload shape', async () => {
    // The exact payload the real backend returned for this bug report.
    api.getCurrentTestSession.mockResolvedValue({
      id: 2,
      project_id: 'ttm_prototype_2',
      source: 'test',
      title: null,
      datetime_start: '2026-08-21T11:12:46.022813+00:00',
      datetime_end: '2026-08-21T11:21:41.034645+00:00',
      start_state: 'Precontemplation',
      end_state: 'Precontemplation',
      open: true,
      active: true,
      has_annotations: false,
      comment: null
    })

    await testChatStore.loadMessages()

    expect(testChatStore.currentProjectId.value).toBe('ttm_prototype_2')
    expect(testChatStore.currentSessionId.value).toBe(2)

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/files/index.css/content?session_id=2'),
      expect.anything()
    )
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
  })
})
