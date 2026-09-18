import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))
vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../../../dialogStore.js', () => ({ confirmDialog: vi.fn() }))
vi.mock('../../../api.js', () => ({
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  projectFileContentUrl: vi.fn((projectId, fileName, sessionId) => `/api/core/projects/${projectId}/files/${fileName}/content?session_id=${sessionId}`)
}))

function skinText() {
  return document.head.querySelector('style')?.textContent ?? null
}

class FakeAppSkin {
  constructor(appId, css) {
    this.appId = appId
    this._css = css
  }

  key() {
    return this.appId
  }

  async css() {
    return this._css
  }
}

describe('who owns the one shared skin element', () => {
  let chatStore
  let chatSkin
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../../../chatStore.js')
    chatSkin = await import('../../../chatSkin.js')
    fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => '.chat-body { color: live; }' })
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  async function liveChatOnScreen() {
    chatStore.currentProjectId.value = 'live-project'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: live; }'))
  }

  it('a panel that takes the skin wins over the chat underneath, which no longer repaints over it', async () => {
    await liveChatOnScreen()

    chatSkin.holdSkin(new FakeAppSkin('app-a', '.chat-body { color: a; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: a; }'))

    chatStore.currentSessionId.value = 2
    chatSkin.invalidateSkin()
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: a; }'))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('the panel left behind by an app switch repaints nothing: the app selected now keeps its skin', async () => {
    await liveChatOnScreen()

    const releaseA = chatSkin.holdSkin(new FakeAppSkin('app-a', '.chat-body { color: a; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: a; }'))

    chatSkin.holdSkin(new FakeAppSkin('app-b', '.chat-body { color: b; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: b; }'))

    releaseA()
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: b; }'))
  })

  it('an app switch reads one skin, not one per panel: the chat underneath is never fetched in between', async () => {
    await liveChatOnScreen()
    fetchMock.mockClear()

    const releaseA = chatSkin.holdSkin(new FakeAppSkin('app-a', '.chat-body { color: a; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: a; }'))

    releaseA()
    chatSkin.holdSkin(new FakeAppSkin('app-b', '.chat-body { color: b; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: b; }'))

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('the last panel to leave hands the element back to the chat underneath', async () => {
    await liveChatOnScreen()

    const release = chatSkin.holdSkin(new FakeAppSkin('app-a', '.chat-body { color: a; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: a; }'))

    release()
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: live; }'))
  })

  it('an app skin answering after another app took over never lands', async () => {
    let answerA = null
    const slowA = {
      key: () => 'app-a',
      css: () => new Promise((resolve) => { answerA = resolve })
    }

    chatSkin.holdSkin(slowA)
    await vi.waitFor(() => expect(answerA).not.toBeNull())

    chatSkin.holdSkin(new FakeAppSkin('app-b', '.chat-body { color: b; }'))
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: b; }'))

    answerA('.chat-body { color: a; }')
    await vi.waitFor(() => expect(skinText()).toContain('.chat-body { color: b; }'))
  })
})

describe('AppSkinSource reads the app store, at the published revision', () => {
  let AppSkinSource
  let fetchMock

  beforeEach(async () => {
    vi.resetModules()
    AppSkinSource = (await import('../appSkinSource.js')).AppSkinSource
    fetchMock = vi.fn()
    global.fetch = fetchMock
  })

  afterEach(() => vi.clearAllMocks())

  it('asks the app-store endpoint for index.css and resolves its asset urls against the app', async () => {
    fetchMock.mockResolvedValue({ ok: true, text: async () => '.chat-body { background-image: url("fondo.jpeg"); }' })

    const css = await new AppSkinSource({ value: 'aprendre_catala' }).css()

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/app-store/apps/aprendre_catala/files/index.css/content'),
      expect.objectContaining({ credentials: 'include', cache: 'no-store' })
    )
    expect(css).toContain('/projects/aprendre_catala/files/media/fondo.jpeg/content')
  })

  it('an app with no index.css leaves nothing to show', async () => {
    fetchMock.mockResolvedValue({ ok: false })

    expect(await new AppSkinSource({ value: 'hello_world' }).css()).toBeNull()
  })
})
