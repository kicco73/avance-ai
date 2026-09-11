import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../api.js', () => ({
  getState: vi.fn().mockResolvedValue({}),
  putProject: vi.fn(),
  postNewProject: vi.fn(),
  activateProject: vi.fn(),
  deleteProject: vi.fn(),
  postWipeAllLiveSessions: vi.fn(),
  postCleanUnusedRevisions: vi.fn(),
  downloadProject: vi.fn(),
  getBackup: vi.fn(),
  postRestoreBackup: vi.fn(),
  getAbout: vi.fn(),
  getPublishPreview: vi.fn(),
  postPublishProject: vi.fn(),
}))
vi.mock('../../../dialogStore.js', () => ({
  aboutDialog: vi.fn(),
  confirmDialog: vi.fn(),
  customDialog: vi.fn(),
  infoDialog: vi.fn(),
}))
vi.mock('../../../chatStore.js', () => ({
  handleStateChange: vi.fn(),
  loadMessages: vi.fn(),
  clearChatUi: vi.fn(),
}))

import { getPublishPreview, postPublishProject } from '../../../api.js'
import { confirmDialog, customDialog, infoDialog } from '../../../dialogStore.js'
import { onProjectsChanged } from '../../../projectChangeEvents.js'
import { useProjectAdminActions } from '../useProjectAdminActions.js'

describe('handlePublishProject', () => {
  let catalogChanges

  function actions() {
    catalogChanges = []
    onProjectsChanged(() => catalogChanges.push('changed'))
    return useProjectAdminActions()
  }

  beforeEach(() => {
    vi.clearAllMocks()
    getPublishPreview.mockResolvedValue({ needs_remap: false, has_active_sessions: false })
    postPublishProject.mockResolvedValue({
      revision: 4, published_revision: 4, built: { module: 'proj_r4', revision: 4 },
    })
  })

  it('reports what the publish compiled, where it compiled anything', async () => {
    await actions().handlePublishProject('proj')

    expect(postPublishProject).toHaveBeenCalledWith('proj', null)
    // Publishing moves the catalog, and whoever displays it observes
    // that fact rather than being poked through a component ref.
    expect(catalogChanges).toEqual(['changed'])
    expect(infoDialog).toHaveBeenCalledWith(expect.objectContaining({
      body: 'Published revision 4. Compiled into proj_r4.'
    }))
  })

  it('reports the publish alone where nothing was compiled', async () => {
    postPublishProject.mockResolvedValue({ revision: 4, published_revision: 4 })

    await actions().handlePublishProject('proj')

    expect(postPublishProject).toHaveBeenCalled()
    expect(infoDialog).toHaveBeenCalledWith(expect.objectContaining({ body: 'Published revision 4.' }))
  })

  it('asks before publishing over an active session, and stops when declined', async () => {
    getPublishPreview.mockResolvedValue({ needs_remap: false, has_active_sessions: true })
    confirmDialog.mockResolvedValue(false)

    await actions().handlePublishProject('proj')

    expect(confirmDialog).toHaveBeenCalled()
    expect(postPublishProject).not.toHaveBeenCalled()
  })

  it('publishes with the state picked in the remap dialog, and stops when it is cancelled', async () => {
    getPublishPreview.mockResolvedValue({ needs_remap: true, missing_state: 'gone', available_states: ['a'] })
    customDialog.mockResolvedValue('a')

    const admin = actions()
    await admin.handlePublishProject('proj')
    expect(postPublishProject).toHaveBeenCalledWith('proj', 'a')

    customDialog.mockResolvedValue(null)
    postPublishProject.mockClear()
    await admin.handlePublishProject('proj')
    expect(postPublishProject).not.toHaveBeenCalled()
  })

  it('stops on a failed publish, without reporting', async () => {
    postPublishProject.mockRejectedValue(new Error('boom'))

    await actions().handlePublishProject('proj')

    expect(infoDialog).not.toHaveBeenCalled()
  })
})
