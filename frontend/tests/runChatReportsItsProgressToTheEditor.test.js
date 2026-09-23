import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('html2canvas', () => ({ default: vi.fn() }))
vi.mock('../src/audio.js', () => ({
  playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn(),
}))
vi.mock('../src/skills/platform/api.js', () => ({
  getTestSessions: vi.fn().mockResolvedValue([]),
  getTestChatModels: vi.fn().mockResolvedValue({ models: [], auto: true, current_index: 0 }),
  postTestChatModelSelection: vi.fn(),
  postResetTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  projectFileContentUrl: vi.fn(() => ''),
}))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue([]),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
  resolveApiUrl: vi.fn((url) => url),
}))

const STATE_A = { key: 'a', ui_label: 'A', actions: [] }

describe('the run chat, the only window the socket answers, and the editor around it', () => {
  let bus
  let testStore
  let app
  let posted

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    posted = []
    vi.spyOn(window.parent, 'postMessage').mockImplementation((data) => posted.push(data))

    testStore = (await import('../src/skills/platform/testChatStore.js')).testStore
    const EmbedTestChat = (await import('../src/skills/platform/components/project/edit/run/EmbedTestChat.vue')).default
    app = createApp(EmbedTestChat, { projectId: 'proj', sessionId: '5' })
    app.mount(document.createElement('div'))
    await nextTick()

    await testStore.selectSession({ id: 5, current: true })
    bus.deliver({
      type: 'session.info', session_id: 5, project_id: 'proj', session_type: 'test',
      state: STATE_A, services: {}, audio: false, current: true, channel: 'webchat', reply_silence_seconds: 45,
    })
  })

  afterEach(() => {
    app?.unmount()
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  function advances() {
    return posted.filter((message) => message.type === 'run-advanced')
  }

  it('reports the values the turn measured, so the editor can refresh what it shows', async () => {
    bus.deliver({ type: 'state.signals', session_id: 5, values: { mood: 0.5 } })
    await nextTick()

    expect(advances()).toEqual([{ source: 'run-chat-embed', type: 'run-advanced' }])
  })

  it('reports a reply landing too, so the editor can catch up on the transcript it never sees', async () => {
    bus.deliver({
      type: 'output.text', session_id: 5, assistant_message_id: 11, text: 'noted.', timestamp: 't',
    })
    await nextTick()

    expect(advances()).toEqual([{ source: 'run-chat-embed', type: 'run-advanced' }])
  })

  it('says nothing about a conversation other than the one it is showing', async () => {
    bus.deliver({ type: 'state.signals', session_id: 99, values: { mood: 0.9 } })
    bus.deliver({
      type: 'output.text', session_id: 99, assistant_message_id: 12, text: 'elsewhere', timestamp: 't',
    })
    await nextTick()

    expect(advances()).toEqual([])
  })
})
