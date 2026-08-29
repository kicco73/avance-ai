import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  getProjectFiles: vi.fn(),
  putProjectFile: vi.fn(),
  putProjectFileBinary: vi.fn(),
  deleteProjectFile: vi.fn(),
  postAddLegalTerms: vi.fn(),
}))
vi.mock('../src/errorStore.js', () => ({
  setApiError: vi.fn(),
  clearApiError: vi.fn(),
}))
vi.mock('../src/dialogStore.js', () => ({
  confirmDialog: vi.fn(),
  promptDialog: vi.fn(),
  chooseDialog: vi.fn(),
}))

import { getProjectFiles, putProjectFile, putProjectFileBinary, deleteProjectFile, postAddLegalTerms } from '../src/api.js'
import { setApiError, clearApiError } from '../src/errorStore.js'
import { confirmDialog, promptDialog, chooseDialog } from '../src/dialogStore.js'
import { useProjectFiles } from '../src/composables/useProjectFiles.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

function fakeFile(name, { size = 10, content = 'hello' } = {}) {
  const file = new File([content], name)
  Object.defineProperty(file, 'size', { value: size })
  file.text = () => Promise.resolve(content)
  return file
}

describe('useProjectFiles', () => {
  let unmount
  const emit = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    getProjectFiles.mockResolvedValue({ files: ['index.yml'] })
  })

  afterEach(() => {
    unmount?.()
  })

  function mount() {
    const mounted = mountComposable(() => useProjectFiles('proj', emit))
    unmount = mounted.unmount
    return mounted.result
  }

  function setEditor(s, { isDirty = false, save = vi.fn().mockResolvedValue(true), discard = vi.fn() } = {}) {
    s.designPanelRef.value = { indexYmlEditorRef: { isDirty, save, discard } }
  }

  it('loadFiles() fetches and stores the file list', async () => {
    getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css'] })
    const s = mount()

    const p = s.loadFiles()
    expect(s.filesLoading.value).toBe(true)
    await p

    expect(s.files.value).toEqual(['index.yml', 'index.css'])
    expect(s.filesLoading.value).toBe(false)
  })

  describe('activeEditor()/activeEditorIsDirty dispatch by open file type', () => {
    it('dispatches to indexYmlEditorRef for index.yml', () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      setEditor(s, { isDirty: true })
      expect(s.activeEditor()).toBe(s.designPanelRef.value.indexYmlEditorRef)
      expect(s.activeEditorIsDirty.value).toBe(true)
    })

    it('dispatches to indexCssEditorRef for index.css', () => {
      const s = mount()
      s.currentFileName.value = 'index.css'
      s.designPanelRef.value = { indexCssEditorRef: { isDirty: true } }
      expect(s.activeEditor()).toBe(s.designPanelRef.value.indexCssEditorRef)
      expect(s.activeEditorIsDirty.value).toBe(true)
    })

    it('an image is never dirty and has no editor', () => {
      const s = mount()
      s.currentFileName.value = 'aspect/logo.png'
      expect(s.activeEditor()).toBeNull()
      expect(s.activeEditorIsDirty.value).toBe(false)
    })

    it('dispatches to mdEditorRef for a .md/.txt attachment', () => {
      const s = mount()
      s.currentFileName.value = 'behaviour/notes.md'
      s.designPanelRef.value = { mdEditorRef: { isDirty: true } }
      expect(s.activeEditor()).toBe(s.designPanelRef.value.mdEditorRef)
      expect(s.activeEditorIsDirty.value).toBe(true)
    })

    it('falls back to codeEditorRef for anything else', () => {
      const s = mount()
      s.currentFileName.value = 'behaviour/script.csv'
      s.designPanelRef.value = { codeEditorRef: { isDirty: false } }
      expect(s.activeEditor()).toBe(s.designPanelRef.value.codeEditorRef)
      expect(s.activeEditorIsDirty.value).toBe(false)
    })
  })

  describe('selectFile', () => {
    it('is a no-op when the file is already open', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      await s.selectFile('index.yml')
      expect(chooseDialog).not.toHaveBeenCalled()
    })

    it('switches immediately when the active editor is not dirty', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      setEditor(s, { isDirty: false })

      await s.selectFile('index.css')

      expect(s.currentFileName.value).toBe('index.css')
      expect(chooseDialog).not.toHaveBeenCalled()
    })

    // guardedAction/runGuardedAction are deliberately fire-and-forget (see
    // selectFile's own lack of `async`) — a bare `await selectFile(...)`
    // only guarantees chooseDialog's own promise settled, not whatever
    // runs after a second `await` inside its branches. vi.waitFor polls
    // instead of assuming a fixed number of microtask ticks.
    it('when dirty and the user picks Save, saves then switches', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      const save = vi.fn().mockResolvedValue(true)
      setEditor(s, { isDirty: true, save })
      chooseDialog.mockResolvedValue('save')

      s.selectFile('index.css')

      await vi.waitFor(() => expect(s.currentFileName.value).toBe('index.css'))
      expect(save).toHaveBeenCalled()
    })

    it('when Save fails (returns falsy), does not switch', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      const save = vi.fn().mockResolvedValue(false)
      setEditor(s, { isDirty: true, save })
      chooseDialog.mockResolvedValue('save')

      s.selectFile('index.css')

      await vi.waitFor(() => expect(save).toHaveBeenCalled())
      expect(s.currentFileName.value).toBe('index.yml')
    })

    it('when dirty and the user picks Discard, discards then switches', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      const discard = vi.fn()
      setEditor(s, { isDirty: true, discard })
      chooseDialog.mockResolvedValue('discard')

      s.selectFile('index.css')

      await vi.waitFor(() => expect(s.currentFileName.value).toBe('index.css'))
      expect(discard).toHaveBeenCalled()
    })

    it('when dirty and the user cancels, stays on the current file', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      setEditor(s, { isDirty: true })
      chooseDialog.mockResolvedValue(null)

      await s.selectFile('index.css')

      expect(s.currentFileName.value).toBe('index.yml')
    })
  })

  describe('handleUploadFile', () => {
    function event(files) {
      return { target: { files, value: 'x' } }
    }

    it('rejects an unsupported extension without uploading anything', async () => {
      const s = mount()
      await s.handleUploadFile(event([fakeFile('virus.exe')]))

      expect(setApiError).toHaveBeenCalled()
      expect(putProjectFile).not.toHaveBeenCalled()
    })

    it('rejects an oversized image without uploading anything', async () => {
      const s = mount()
      await s.handleUploadFile(event([fakeFile('big.png', { size: 6 * 1024 * 1024 })]))

      expect(setApiError).toHaveBeenCalled()
      expect(putProjectFileBinary).not.toHaveBeenCalled()
    })

    it('uploads a text file under behaviour/, reloads, and selects it', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml' // clean, so selectFile switches immediately
      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'behaviour/notes.md'] })

      await s.handleUploadFile(event([fakeFile('notes.md')]))

      expect(putProjectFile).toHaveBeenCalledWith('proj', 'behaviour/notes.md', 'hello')
      expect(s.currentFileName.value).toBe('behaviour/notes.md')
      expect(clearApiError).toHaveBeenCalled()
    })

    it('uploads an image under aspect/ as binary', async () => {
      const s = mount()
      const file = fakeFile('logo.png')
      await s.handleUploadFile(event([file]))

      expect(putProjectFileBinary).toHaveBeenCalledWith('proj', 'aspect/logo.png', file)
    })

    it('resets the input value so re-selecting the same file re-fires change', async () => {
      const s = mount()
      const evt = event([fakeFile('notes.md')])
      await s.handleUploadFile(evt)
      expect(evt.target.value).toBe('')
    })
  })

  describe('handleNewAttachment', () => {
    it('cancelling the prompt creates nothing', async () => {
      promptDialog.mockResolvedValue(null)
      const s = mount()
      await s.handleNewAttachment()
      expect(putProjectFile).not.toHaveBeenCalled()
    })

    it('creates behaviour/<name>.md from the prompted name', async () => {
      promptDialog.mockResolvedValue('my notes')
      const s = mount()

      await s.handleNewAttachment()

      expect(putProjectFile).toHaveBeenCalledWith('proj', 'behaviour/my notes.md', '')
    })

    it("the prompt's own validate() rejects a duplicate name", async () => {
      getProjectFiles.mockResolvedValue({ files: ['behaviour/notes.md'] })
      const s = mount()
      await s.loadFiles()
      promptDialog.mockImplementation(({ validate }) => {
        expect(validate('notes')).toMatch(/already exists/)
        return Promise.resolve(null)
      })

      await s.handleNewAttachment()
    })
  })

  describe('handleNewAspect', () => {
    it('is a no-op if index.css already exists', async () => {
      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css'] })
      const s = mount()
      await s.loadFiles()

      await s.handleNewAspect()

      expect(putProjectFile).not.toHaveBeenCalled()
    })

    it('creates index.css with the skeleton otherwise', async () => {
      const s = mount()
      await s.handleNewAspect()
      expect(putProjectFile).toHaveBeenCalledWith('proj', 'index.css', expect.stringContaining('.chat-header'))
    })
  })

  describe('handleNewLegal', () => {
    it('is a no-op if legal/terms.md already exists', async () => {
      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'legal/terms.md'] })
      const s = mount()
      await s.loadFiles()

      await s.handleNewLegal()

      expect(postAddLegalTerms).not.toHaveBeenCalled()
    })

    it('creates it via postAddLegalTerms, reloads, and selects it otherwise', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'legal/terms.md'] })

      await s.handleNewLegal()

      expect(postAddLegalTerms).toHaveBeenCalledWith('proj')
      expect(s.currentFileName.value).toBe('legal/terms.md')
    })
  })

  describe('handleDeleteFile', () => {
    it('refuses to delete index.yml without even confirming', async () => {
      const s = mount()
      await s.handleDeleteFile('index.yml')
      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteProjectFile).not.toHaveBeenCalled()
    })

    it('does nothing if the confirm is declined', async () => {
      confirmDialog.mockResolvedValue(false)
      const s = mount()
      await s.handleDeleteFile('behaviour/notes.md')
      expect(deleteProjectFile).not.toHaveBeenCalled()
    })

    it('an image skips the confirm dialog entirely', async () => {
      const s = mount()
      await s.handleDeleteFile('aspect/logo.png')
      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteProjectFile).toHaveBeenCalledWith('proj', 'aspect/logo.png')
    })

    it('deletes, reloads, and switches to index.yml if the deleted file was open', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount()
      s.currentFileName.value = 'behaviour/notes.md'

      await s.handleDeleteFile('behaviour/notes.md')

      expect(deleteProjectFile).toHaveBeenCalledWith('proj', 'behaviour/notes.md')
      expect(s.currentFileName.value).toBe('index.yml')
    })

    it('leaves the open file alone when a different file is deleted', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount()
      s.currentFileName.value = 'behaviour/other.md'

      await s.handleDeleteFile('behaviour/notes.md')

      expect(s.currentFileName.value).toBe('behaviour/other.md')
    })

    it('deleting index.css also switches away if a cascaded theme asset was open', async () => {
      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css', 'aspect/logo.png'] })
      confirmDialog.mockResolvedValue(true)
      const s = mount()
      await s.loadFiles()
      s.currentFileName.value = 'aspect/logo.png'

      await s.handleDeleteFile('index.css')

      expect(s.currentFileName.value).toBe('index.yml')
    })
  })

  it('handleFileSaved emits "saved"', () => {
    const s = mount()
    s.handleFileSaved()
    expect(emit).toHaveBeenCalledWith('saved')
  })

  describe('jumpToDefinition', () => {
    const indexYml = 'states:\n  greeting:\n    contextual-prompt: hi\n'

    it('switches to index.yml first if another file is open, then jumps', async () => {
      const s = mount()
      s.currentFileName.value = 'index.css'
      setEditor(s, { isDirty: false })
      s.designPanelRef.value.indexYmlEditorRef.content = indexYml
      s.designPanelRef.value.indexYmlEditorRef.jumpToLine = vi.fn()

      await s.jumpToDefinition({ kind: 'state', stateKey: 'greeting' })

      expect(s.currentFileName.value).toBe('index.yml')
      expect(s.designPanelRef.value.indexYmlEditorRef.jumpToLine).toHaveBeenCalledWith(1)
    })

    it('silent mode never switches away from a non-index.yml file', async () => {
      const s = mount()
      s.currentFileName.value = 'index.css'

      await s.jumpToDefinition({ kind: 'state', stateKey: 'greeting' }, { silent: true })

      expect(s.currentFileName.value).toBe('index.css')
    })

    it('jumps directly when index.yml is already open', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      s.designPanelRef.value = { indexYmlEditorRef: { content: indexYml, jumpToLine: vi.fn(), isDirty: false } }

      await s.jumpToDefinition({ kind: 'state', stateKey: 'greeting' })

      expect(s.designPanelRef.value.indexYmlEditorRef.jumpToLine).toHaveBeenCalledWith(1)
    })

    it('does nothing (no throw) when the target cannot be located', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      const jumpToLine = vi.fn()
      s.designPanelRef.value = { indexYmlEditorRef: { content: indexYml, jumpToLine, isDirty: false } }

      await s.jumpToDefinition({ kind: 'state', stateKey: 'nope' })

      expect(jumpToLine).not.toHaveBeenCalled()
    })
  })
})
