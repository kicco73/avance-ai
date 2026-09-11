import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../../../api.js', () => ({
  getProjectRevision: vi.fn(),
  postRevertProject: vi.fn(),
}))
vi.mock('../../../dialogStore.js', () => ({
  confirmDialog: vi.fn(),
}))

import { getProjectRevision, postRevertProject } from '../../../api.js'
import { confirmDialog } from '../../../dialogStore.js'
import { useProjectRevision } from '../useProjectRevision.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

describe('useProjectRevision', () => {
  let unmount

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    unmount?.()
  })

  function mount({ fileName = 'index.yml', reload = vi.fn() } = {}) {
    const currentFileName = ref(fileName)
    const activeEditor = () => ({ reload })
    const selectedGraphElement = ref({ kind: 'state', data: { id: 'x' } })
    const mounted = mountComposable(() => useProjectRevision('proj', currentFileName, activeEditor, selectedGraphElement))
    unmount = mounted.unmount
    return { ...mounted.result, currentFileName, selectedGraphElement, reload }
  }

  async function mountAt(revision, published_revision, options = {}) {
    getProjectRevision.mockResolvedValue({ revision, published_revision })
    const s = mount(options)
    await s.refreshProjectRevision()
    return s
  }

  it('refreshProjectRevision stores the revision info, from which canRevert follows', async () => {
    const upToDate = await mountAt(2, 2)
    expect(upToDate.projectRevision.value).toEqual({ revision: 2, published_revision: 2 })
    expect(upToDate.canRevert.value).toBe(false)
    upToDate.unmount?.()

    // canRevert requires a draft ahead AND a real prior publication.
    const ahead = await mountAt(3, 2)
    expect(ahead.canRevert.value).toBe(true)

    getProjectRevision.mockResolvedValue({ revision: 1, published_revision: null })
    await ahead.refreshProjectRevision()
    expect(ahead.canRevert.value).toBe(false)
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
      expect(s.reverting.value).toBe(false)
    })

    // refreshAfterProjectEdit already covers index.yml itself.
    it('leaves index.yml to its own refresh', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = await mountAt(3, 2, { fileName: 'index.yml' })

      await s.handleRevert()

      expect(s.reload).not.toHaveBeenCalled()
    })
  })
})
