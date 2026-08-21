// Real, executable verification of chatStore.js's shared index.css "skin"
// loader (see its own module-level watch/loadSkin) — not just a read of
// the code. Drives the actual exported refs the way TestChat.vue/
// ChatWindow.vue really do, mocks only fetch, and asserts on the real
// jsdom document.head: does a request actually go out, does a <style>
// tag actually land, does toggling applyAspect back on actually resume
// loading, is there ever more than one tag at once.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))
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
  postReset: vi.fn(),
  postTruncateSession: vi.fn(),
  projectFileContentUrl: vi.fn((projectName, fileName, sessionId) => `/api/projects/${projectName}/files/${fileName}/content?session_id=${sessionId}`)
}))

function currentSkinStyleTags() {
  return Array.from(document.head.querySelectorAll('style'))
}

describe("chatStore.js's shared index.css skin loader", () => {
  let chatStore
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../src/chatStore.js')
    fetchMock = vi.fn()
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  it('does nothing at boot — no project/session yet', async () => {
    await nextTick()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(currentSkinStyleTags()).toHaveLength(0)
  })

  it('fetches and applies the skin once a project+session are set', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: red; }' })

    chatStore.currentProjectName.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/proj/files/index.css/content?session_id=1',
      expect.objectContaining({ credentials: 'include', cache: 'no-store' })
    )
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
    expect(currentSkinStyleTags()[0].textContent).toBe('.chat-window-shell { color: red; }')
  })

  it('re-fetches on every skinVersion bump, replacing the same tag rather than adding a second one', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: red; }' })
    chatStore.currentProjectName.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))

    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: blue; }' })
    chatStore.invalidateSkin()
    await vi.waitFor(() => expect(currentSkinStyleTags()[0]?.textContent).toBe('body { color: blue; }'))

    expect(currentSkinStyleTags()).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('applyAspect turned off skips the fetch entirely and removes any applied skin', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: red; }' })
    chatStore.currentProjectName.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))

    chatStore.applyAspect.value = false
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(0))
    expect(fetchMock).toHaveBeenCalledTimes(1) // no second call — applyAspect off short-circuits before fetch
  })

  it('turning applyAspect back on resumes loading — the exact toggle TestChat.vue exposes', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: green; }' })
    chatStore.currentProjectName.value = 'proj'
    chatStore.currentSessionId.value = 1
    chatStore.applyAspect.value = false
    await nextTick()
    expect(currentSkinStyleTags()).toHaveLength(0)

    chatStore.applyAspect.value = true
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
    expect(currentSkinStyleTags()[0].textContent).toBe('body { color: green; }')
  })

  it('a non-ok response (e.g. no index.css yet) clears the skin instead of leaving a stale tag', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: red; }' })
    chatStore.currentProjectName.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))

    fetchMock.mockResolvedValue({ ok: false })
    chatStore.invalidateSkin()
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(0))
  })

  it('switching project+session (e.g. leaving then re-entering Test) fetches the new one', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: red; }' })
    chatStore.currentProjectName.value = 'proj-a'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    fetchMock.mockResolvedValue({ ok: true, text: async () => 'body { color: purple; }' })
    chatStore.currentProjectName.value = 'proj-b'
    chatStore.currentSessionId.value = 2
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/projects/proj-b/files/index.css/content?session_id=2',
      expect.anything()
    )
    expect(currentSkinStyleTags()).toHaveLength(1)
    expect(currentSkinStyleTags()[0].textContent).toBe('body { color: purple; }')
  })
})

describe('the real Test-mode bootstrap sequence (loadMessages -> ensureSession) actually reaches loadSkin', () => {
  let chatStore
  let api
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    api.getMessages.mockResolvedValue([])
    fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: teal; }' })
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  it('entering Test mode (testModeProjectName set, loadMessages called) fetches and applies the skin, using the exact real session payload shape', async () => {
    // The exact payload the real backend returned for this bug report.
    api.getCurrentTestSession.mockResolvedValue({
      id: 2,
      project_name: 'TTM prototype 2',
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

    chatStore.testModeProjectName.value = 'TTM prototype 2'
    await chatStore.loadMessages()

    expect(chatStore.currentProjectName.value).toBe('TTM prototype 2')
    expect(chatStore.currentSessionId.value).toBe(2)

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/files/index.css/content?session_id=2'),
      expect.anything()
    )
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
  })
})
