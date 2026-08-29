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

  it('refreshProjectRevision fetches and stores the revision info', async () => {
    getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
    const s = mount()

    await s.refreshProjectRevision()

    expect(s.projectRevision.value).toEqual({ revision: 3, published_revision: 2 })
  })

  describe('publishUpToDate / canRevert', () => {
    it('is true only once revision === published_revision', async () => {
      getProjectRevision.mockResolvedValue({ revision: 2, published_revision: 2 })
      const s = mount()
      await s.refreshProjectRevision()
      expect(s.publishUpToDate.value).toBe(true)
      expect(s.canRevert.value).toBe(false)
    })

    it('canRevert requires a draft ahead AND a real prior publication', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      const s = mount()
      await s.refreshProjectRevision()
      expect(s.canRevert.value).toBe(true)

      getProjectRevision.mockResolvedValue({ revision: 1, published_revision: null })
      await s.refreshProjectRevision()
      expect(s.canRevert.value).toBe(false)
    })
  })

  describe('handlePublish', () => {
    it('is a no-op when already up to date', async () => {
      getProjectRevision.mockResolvedValue({ revision: 2, published_revision: 2 })
      const s = mount()
      await s.refreshProjectRevision()

      await s.handlePublish()

      expect(getPublishPreview).not.toHaveBeenCalled()
    })

    it('needs_remap opens the remap prompt instead of publishing', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      getPublishPreview.mockResolvedValue({ needs_remap: true, missing_state: 'gone', available_states: ['a', 'b'] })
      const s = mount()
      await s.refreshProjectRevision()

      await s.handlePublish()

      expect(s.publishRemapPrompt.value).toMatchObject({ missing_state: 'gone' })
      expect(postPublishProject).not.toHaveBeenCalled()
    })

    it('an active session asks for confirmation first; declining aborts the publish', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      getPublishPreview.mockResolvedValue({ needs_remap: false, has_active_sessions: true })
      confirmDialog.mockResolvedValue(false)
      const s = mount()
      await s.refreshProjectRevision()

      await s.handlePublish()

      expect(postPublishProject).not.toHaveBeenCalled()
    })

    it('publishes, refreshes the active editor history (for a non-index.yml file), and updates the revision', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      const reload = vi.fn()
      const s = mount({ fileName: 'index.css', reload })
      await s.refreshProjectRevision()

      await s.handlePublish()

      expect(postPublishProject).toHaveBeenCalledWith('proj')
      expect(s.projectRevision.value).toEqual({ revision: 2, published_revision: 2 })
      expect(reload).toHaveBeenCalled()
    })

    it('never reloads the editor when index.yml itself is open (refreshAfterProjectEdit already covers it)', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      const reload = vi.fn()
      const s = mount({ fileName: 'index.yml', reload })
      await s.refreshProjectRevision()

      await s.handlePublish()

      expect(reload).not.toHaveBeenCalled()
    })

    it('runs the pending leave action on success', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      const s = mount()
      await s.refreshProjectRevision()
      const onLeave = vi.fn()
      s.pendingLeaveAction.value = onLeave

      await s.handlePublish()

      expect(onLeave).toHaveBeenCalled()
      expect(s.pendingLeaveAction.value).toBeNull()
    })

    it('clears the pending leave action without running it if the publish fails', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      postPublishProject.mockRejectedValue(new Error('boom'))
      const s = mount()
      await s.refreshProjectRevision()
      const onLeave = vi.fn()
      s.pendingLeaveAction.value = onLeave

      await s.handlePublish()

      expect(onLeave).not.toHaveBeenCalled()
      expect(s.pendingLeaveAction.value).toBeNull()
    })
  })

  describe('confirmPublishRemap', () => {
    it('publishes with the chosen remap target, closes the prompt, and runs the pending leave action', async () => {
      const s = mount()
      s.publishRemapPrompt.value = { missing_state: 'gone', available_states: ['a'] }
      const onLeave = vi.fn()
      s.pendingLeaveAction.value = onLeave

      await s.confirmPublishRemap('a')

      expect(postPublishProject).toHaveBeenCalledWith('proj', 'a')
      expect(s.publishRemapPrompt.value).toBeNull()
      expect(onLeave).toHaveBeenCalled()
    })

    it('leaves the prompt open on failure so the user can retry or cancel', async () => {
      postPublishProject.mockRejectedValue(new Error('boom'))
      const s = mount()
      s.publishRemapPrompt.value = { missing_state: 'gone', available_states: ['a'] }

      await s.confirmPublishRemap('a')

      expect(s.publishRemapPrompt.value).not.toBeNull()
    })
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
    it('does nothing when canRevert is false', async () => {
      getProjectRevision.mockResolvedValue({ revision: 1, published_revision: null })
      const s = mount()
      await s.refreshProjectRevision()

      await s.handleRevert()

      expect(confirmDialog).not.toHaveBeenCalled()
    })

    it('does nothing without confirmation', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      confirmDialog.mockResolvedValue(false)
      const s = mount()
      await s.refreshProjectRevision()

      await s.handleRevert()

      expect(postRevertProject).not.toHaveBeenCalled()
    })

    it('reverts, clears the graph selection, and refreshes the active editor', async () => {
      getProjectRevision.mockResolvedValue({ revision: 3, published_revision: 2 })
      confirmDialog.mockResolvedValue(true)
      const reload = vi.fn()
      const s = mount({ fileName: 'index.css', reload })
      await s.refreshProjectRevision()

      await s.handleRevert()

      expect(postRevertProject).toHaveBeenCalledWith('proj')
      expect(s.selectedGraphElement.value).toBeNull()
      expect(reload).toHaveBeenCalled()
      expect(s.publishing.value).toBe(false)
    })
  })
})
