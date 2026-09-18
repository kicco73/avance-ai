import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true), customDialog: vi.fn() }))
vi.mock('../src/api.js', () => ({
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
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))

const STATE = { key: 'x', ui_label: 'X', actions: [] }
const AUDIO_URL = '/api/core/projects/text_adventure/files/media/title.mp3/content'

describe('background audio is scoped per chat store, not shared globally', () => {
  let bus
  let liveStore
  let previewStore

  beforeEach(async () => {
    vi.resetModules()
    HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
    HTMLMediaElement.prototype.pause = vi.fn()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    const { createChatStore } = await import('../src/chatStoreFactory.js')
    const api = await import('../src/api.js')
    api.getHistory.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])

    liveStore = createChatStore({ kind: 'live', getSessionsList: vi.fn().mockResolvedValue([]) })
    previewStore = createChatStore({ kind: 'preview', getSessionsList: vi.fn().mockResolvedValue([]) })

    await liveStore.loadMessages('proj')
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE })

    await previewStore.loadMessages('proj')
    bus.deliverEntered({ sessionId: 8, projectId: 'proj', sessionType: 'preview', state: STATE })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('activates only the store whose own session asked for audio', () => {
    bus.deliver({ type: 'ui.notification', session_id: 7, project_id: 'proj', task: `show_media('${AUDIO_URL}')` })

    expect(liveStore.backgroundAudioUrl.value).toContain('title.mp3')
    expect(liveStore.backgroundAudioPlaying.value).toBe(false)
    expect(previewStore.backgroundAudioUrl.value).toBeNull()
  })

  it('lets each store toggle play/pause independently', () => {
    bus.deliver({ type: 'ui.notification', session_id: 7, project_id: 'proj', task: `show_media('${AUDIO_URL}')` })
    bus.deliver({ type: 'ui.notification', session_id: 8, project_id: 'proj', task: `show_media('${AUDIO_URL}')` })

    liveStore.toggleBackgroundAudio()

    expect(liveStore.backgroundAudioPlaying.value).toBe(true)
    expect(previewStore.backgroundAudioPlaying.value).toBe(false)
  })

  it('forgets the music (button disappears) when its own store leaves the session', () => {
    bus.deliver({ type: 'ui.notification', session_id: 7, project_id: 'proj', task: `show_media('${AUDIO_URL}')` })
    expect(liveStore.backgroundAudioUrl.value).not.toBeNull()

    bus.deliver({ type: 'session.ended', session_id: 7, reason: 'final-state' })

    expect(liveStore.backgroundAudioUrl.value).toBeNull()
    expect(liveStore.backgroundAudioPlaying.value).toBe(false)
  })
})
