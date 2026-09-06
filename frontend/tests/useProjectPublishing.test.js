import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  getProjectRevision: vi.fn(),
  getPublishPreview: vi.fn(),
  postPublishProject: vi.fn(),
  postRevertProject: vi.fn(),
}))
vi.mock('../src/dialogStore.js', () => ({
  confirmDialog: vi.fn(),
}))

import { getProjectRevision, getPublishPreview, postPublishProject, postRevertProject } from '../src/api.js'
import { confirmDialog } from '../src/dialogStore.js'
import { useProjectPublishing } from '../src/composables/useProjectPublishing.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

describe('useProjectPublishing', () => {
  let unmount

  beforeEach(() => {
    vi.clearAllMocks()
    getPublishPreview.mockResolvedValue({ needs_remap: false, has_active_sessions: false })
    postPublishProject.mockResolvedValue({ revision: 2, published_revision: 2 })
  })

  afterEach(() => {
    unmount?.()
  })

  function mount({ fileName = 'index.yml', reload = vi.fn() } = {}) {
    const currentFileName = ref(fileName)
    const activeEditor = () => ({ reload })
    const selectedGraphElement = ref({ kind: 'state', data: { id: 'x' } })
    const mounted = mountComposable(() => useProjectPublishing('proj', currentFileName, activeEditor, selectedGraphElement))
    unmount = mounted.unmount
    return { ...mounted.result, currentFileName, selectedGraphElement, reload }
  }

  async function mountAt(revision, published_revision, options = {}) {
    getProjectRevision.mockResolvedValue({ revision, published_revision })
    const s = mount(options)
    await s.refreshProjectRevision()
    return s
  }

  it('refreshProjectRevision stores the revision info, from which publishUpToDate and canRevert follow', async () => {
    const upToDate = await mountAt(2, 2)
    expect(upToDate.projectRevision.value).toEqual({ revision: 2, published_revision: 2 })
    expect(upToDate.publishUpToDate.value).toBe(true)
    expect(upToDate.canRevert.value).toBe(false)
    upToDate.unmount?.()

    // canRevert requires a draft ahead AND a real prior publication.
    const ahead = await mountAt(3, 2)
    expect(ahead.publishUpToDate.value).toBe(false)
    expect(ahead.canRevert.value).toBe(true)

    getProjectRevision.mockResolvedValue({ revision: 1, published_revision: null })
    await ahead.refreshProjectRevision()
    expect(ahead.canRevert.value).toBe(false)
  })

  describe('handlePublish', () => {
    it('does nothing when already up to date, when a remap is needed, or when an active-session confirm is declined', async () => {
      const upToDate = await mountAt(2, 2)
      await upToDate.handlePublish()
      expect(getPublishPreview).not.toHaveBeenCalled()
      upToDate.unmount?.()

      getPublishPreview.mockResolvedValue({ needs_remap: true, missing_state: 'gone', available_states: ['a', 'b'] })
      const remap = await mountAt(3, 2)
      await remap.handlePublish()
      expect(remap.publishRemapPrompt.value).toMatchObject({ missing_state: 'gone' })
      expect(postPublishProject).not.toHaveBeenCalled()
      remap.unmount?.()

      getPublishPreview.mockResolvedValue({ needs_remap: false, has_active_sessions: true })
      confirmDialog.mockResolvedValue(false)
      const active = await mountAt(3, 2)
      await active.handlePublish()
      expect(postPublishProject).not.toHaveBeenCalled()
    })

    it('publishes, updates the revision, and reloads the active editor only for a file other than index.yml', async () => {
      const css = await mountAt(3, 2, { fileName: 'index.css' })

      await css.handlePublish()

      expect(postPublishProject).toHaveBeenCalledWith('proj')
      expect(css.projectRevision.value).toEqual({ revision: 2, published_revision: 2 })
      expect(css.reload).toHaveBeenCalled()
      css.unmount?.()

      // refreshAfterProjectEdit already covers index.yml itself.
      const yml = await mountAt(3, 2, { fileName: 'index.yml' })
      await yml.handlePublish()
      expect(yml.reload).not.toHaveBeenCalled()
    })

    it('runs the pending leave action on success and clears it without running it on failure', async () => {
      const ok = await mountAt(3, 2)
      const onLeave = vi.fn()
      ok.pendingLeaveAction.value = onLeave

      await ok.handlePublish()
      expect(onLeave).toHaveBeenCalled()
      expect(ok.pendingLeaveAction.value).toBeNull()
      ok.unmount?.()

      postPublishProject.mockRejectedValue(new Error('boom'))
      const failing = await mountAt(3, 2)
      const notRun = vi.fn()
      failing.pendingLeaveAction.value = notRun

      await failing.handlePublish()
      expect(notRun).not.toHaveBeenCalled()
      expect(failing.pendingLeaveAction.value).toBeNull()
    })
  })

  it('confirmPublishRemap publishes with the chosen target and closes the prompt, leaving it open on failure', async () => {
    const s = mount()
    s.publishRemapPrompt.value = { missing_state: 'gone', available_states: ['a'] }
    const onLeave = vi.fn()
    s.pendingLeaveAction.value = onLeave

    await s.confirmPublishRemap('a')

    expect(postPublishProject).toHaveBeenCalledWith('proj', 'a')
    expect(s.publishRemapPrompt.value).toBeNull()
    expect(onLeave).toHaveBeenCalled()

    postPublishProject.mockRejectedValue(new Error('boom'))
    s.publishRemapPrompt.value = { missing_state: 'gone', available_states: ['a'] }
    await s.confirmPublishRemap('a')
    expect(s.publishRemapPrompt.value).not.toBeNull()
  })

  it('cancelPublishRemap closes the prompt and clears the pending leave action', () => {
    const s = mount()
    s.publishRemapPrompt.value = { missing_state: 'gone', available_states: ['a'] }
    s.pendingLeaveAction.value = vi.fn()

    s.cancelPublishRemap()

    expect(s.publishRemapPrompt.value).toBeNull()
    expect(s.pendingLeaveAction.value).toBeNull()
    expect(s.publishing.value).toBe(false)
  })

  describe('handleRevert', () => {
    it('does nothing when canRevert is false or the confirm is declined', async () => {
      const nothingToRevert = await mountAt(1, null)
      await nothingToRevert.handleRevert()
      expect(confirmDialog).not.toHaveBeenCalled()
      nothingToRevert.unmount?.()

      confirmDialog.mockResolvedValue(false)
      const declined = await mountAt(3, 2)
      await declined.handleRevert()
      expect(postRevertProject).not.toHaveBeenCalled()
    })

    it('reverts, clears the graph selection, and refreshes the active editor', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = await mountAt(3, 2, { fileName: 'index.css' })

      await s.handleRevert()

      expect(postRevertProject).toHaveBeenCalledWith('proj')
      expect(s.selectedGraphElement.value).toBeNull()
      expect(s.reload).toHaveBeenCalled()
      expect(s.publishing.value).toBe(false)
    })
  })
})
