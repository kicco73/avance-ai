import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))
vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../api.js', () => ({ deleteSession: vi.fn(), deletePreviewSessionEnv: vi.fn() }))
vi.mock('../../../api.js', () => ({ getHistory: vi.fn().mockResolvedValue([]) }))

async function previewOpenOn(sessionId) {
  const bus = await import('../../../../tests/fakeBus.js')
  bus.resetFakeBus()
  const preview = await import('../appStorePreviewStore.js')
  const taskActions = await import('../../../taskActions.js')
  preview.setPreviewApp('proj')
  await preview.handleNewSession()
  bus.deliverEntered({ sessionId, projectId: 'proj', sessionType: 'preview' })
  bus.deliver({ type: 'ui.notification', session_id: sessionId, project_id: 'proj', task: 'celebrate()' })
  return taskActions.runTaskScript
}

describe('a task pushed to a preview conversation runs once, inside the chat that shows it', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('does not run in the page that hosts the preview frame', async () => {
    const runTaskScript = await previewOpenOn(5)

    expect(runTaskScript).not.toHaveBeenCalled()
  })

  it('runs in the preview frame', async () => {
    vi.stubGlobal('top', {})

    const runTaskScript = await previewOpenOn(5)

    expect(runTaskScript).toHaveBeenCalledWith('celebrate()', expect.anything())
  })
})
