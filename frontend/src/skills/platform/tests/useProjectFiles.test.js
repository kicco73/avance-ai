import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../../../api.js', () => ({
  getProjectFiles: vi.fn(),
  getProjectFileTypes: vi.fn(),
  putProjectFile: vi.fn(),
  putProjectFileBinary: vi.fn(),
  deleteProjectFile: vi.fn(),
  postAddLegalTerms: vi.fn(),
}))
vi.mock('../../../errorStore.js', () => ({
  setApiError: vi.fn(),
  clearApiError: vi.fn(),
}))
vi.mock('../../../dialogStore.js', () => ({
  confirmDialog: vi.fn(),
  promptDialog: vi.fn(),
  chooseDialog: vi.fn(),
}))

import { getProjectFiles, getProjectFileTypes, putProjectFile, putProjectFileBinary, deleteProjectFile, postAddLegalTerms } from '../../../api.js'
import { setApiError, clearApiError } from '../../../errorStore.js'
import { confirmDialog, promptDialog, chooseDialog } from '../../../dialogStore.js'
import { useProjectFiles } from '../useProjectFiles.js'
import { ensureProjectFileTypes } from '../../../projectFileTypes.js'
import { PROJECT_FILE_TYPES_PAYLOAD } from '../../../../tests/projectFileTypesFixture.js'

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

  beforeEach(async () => {
    vi.clearAllMocks()
    getProjectFiles.mockResolvedValue({ files: ['index.yml'] })
    getProjectFileTypes.mockResolvedValue(PROJECT_FILE_TYPES_PAYLOAD)
    await ensureProjectFileTypes()
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

  function uploadEvent(files) {
    return { target: { files, value: 'x' } }
  }

  it('loadFiles() fetches and stores the file list, reporting progress while in flight', async () => {
    getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css'] })
    const s = mount()

    const p = s.loadFiles()
    expect(s.filesLoading.value).toBe(true)
    await p

    expect(s.files.value).toEqual(['index.yml', 'index.css'])
    expect(s.filesLoading.value).toBe(false)
  })

  it('activeEditor()/activeEditorIsDirty dispatch by open file type, with media never dirty and editorless', () => {
    const s = mount()

    s.currentFileName.value = 'index.yml'
    setEditor(s, { isDirty: true })
    expect(s.activeEditor()).toBe(s.designPanelRef.value.indexYmlEditorRef)
    expect(s.activeEditorIsDirty.value).toBe(true)

    for (const [fileName, refName] of [
      ['index.css', 'indexCssEditorRef'],
      ['behaviour/notes.md', 'mdEditorRef'],
      ['behaviour/script.csv', 'codeEditorRef'],
    ]) {
      s.currentFileName.value = fileName
      s.designPanelRef.value = { [refName]: { isDirty: true } }
      expect(s.activeEditor()).toBe(s.designPanelRef.value[refName])
      expect(s.activeEditorIsDirty.value).toBe(true)
    }

    for (const mediaName of ['aspect/logo.png', 'aspect/title.mp3']) {
      s.currentFileName.value = mediaName
      expect(s.activeEditor()).toBeNull()
      expect(s.activeEditorIsDirty.value).toBe(false)
    }
  })

  describe('selectFile', () => {
    it('switches with no dialog when the file is already open or the active editor is clean', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'

      await s.selectFile('index.yml')
      expect(chooseDialog).not.toHaveBeenCalled()

      setEditor(s, { isDirty: false })
      await s.selectFile('index.css')
      expect(s.currentFileName.value).toBe('index.css')
      expect(chooseDialog).not.toHaveBeenCalled()
    })

    it('when dirty, Save and Discard both switch while a failed save or a cancel stays put', async () => {
      const saved = mount()
      saved.currentFileName.value = 'index.yml'
      const save = vi.fn().mockResolvedValue(true)
      setEditor(saved, { isDirty: true, save })
      chooseDialog.mockResolvedValue('save')
      saved.selectFile('index.css')
      await vi.waitFor(() => expect(saved.currentFileName.value).toBe('index.css'))
      expect(save).toHaveBeenCalled()
      saved.unmount?.()

      const discarded = mount()
      discarded.currentFileName.value = 'index.yml'
      const discard = vi.fn()
      setEditor(discarded, { isDirty: true, discard })
      chooseDialog.mockResolvedValue('discard')
      discarded.selectFile('index.css')
      await vi.waitFor(() => expect(discarded.currentFileName.value).toBe('index.css'))
      expect(discard).toHaveBeenCalled()

      const failed = mount()
      failed.currentFileName.value = 'index.yml'
      const failingSave = vi.fn().mockResolvedValue(false)
      setEditor(failed, { isDirty: true, save: failingSave })
      chooseDialog.mockResolvedValue('save')
      failed.selectFile('index.css')
      await vi.waitFor(() => expect(failingSave).toHaveBeenCalled())
      expect(failed.currentFileName.value).toBe('index.yml')

      const cancelled = mount()
      cancelled.currentFileName.value = 'index.yml'
      setEditor(cancelled, { isDirty: true })
      chooseDialog.mockResolvedValue(null)
      await cancelled.selectFile('index.css')
      expect(cancelled.currentFileName.value).toBe('index.yml')
    })
  })

  describe('handleUploadFile', () => {
    it('rejects an unsupported extension or a file past its own type limit without uploading anything', async () => {
      const s = mount()

      await s.handleUploadFile(uploadEvent([fakeFile('virus.exe')]))
      expect(setApiError).toHaveBeenCalled()
      expect(putProjectFile).not.toHaveBeenCalled()

      await s.handleUploadFile(uploadEvent([fakeFile('big.png', { size: 6 * 1024 * 1024 })]))
      expect(putProjectFileBinary).not.toHaveBeenCalled()

      await s.handleUploadFile(uploadEvent([fakeFile('huge.mp3', { size: 16 * 1024 * 1024 })]))
      expect(putProjectFileBinary).not.toHaveBeenCalled()

      await s.handleUploadFile(uploadEvent([fakeFile('stray.yml')]))
      expect(putProjectFile).not.toHaveBeenCalled()
    })

    it('routes a text file to behaviour/ and an image to media/ as binary, then reloads, selects it and resets the input', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'behaviour/notes.md'] })

      const textEvent = uploadEvent([fakeFile('notes.md')])
      await s.handleUploadFile(textEvent)

      expect(putProjectFile).toHaveBeenCalledWith('proj', 'behaviour/notes.md', 'hello')
      expect(s.currentFileName.value).toBe('behaviour/notes.md')
      expect(clearApiError).toHaveBeenCalled()
      expect(textEvent.target.value).toBe('')

      const image = fakeFile('logo.png')
      await s.handleUploadFile(uploadEvent([image]))
      expect(putProjectFileBinary).toHaveBeenCalledWith('proj', 'media/logo.png', image)

      const audio = fakeFile('title.mp3', { size: 6 * 1024 * 1024 })
      await s.handleUploadFile(uploadEvent([audio]))
      expect(putProjectFileBinary).toHaveBeenCalledWith('proj', 'media/title.mp3', audio)
    })
  })

  describe('handleNewAttachment', () => {
    it('creates behaviour/<name>.md from the prompted name, creating nothing when cancelled', async () => {
      promptDialog.mockResolvedValue(null)
      const s = mount()
      await s.handleNewAttachment()
      expect(putProjectFile).not.toHaveBeenCalled()

      promptDialog.mockResolvedValue('my notes')
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

  it('handleNewLegal creates its file once, then selects it, and is a no-op if it already exists', async () => {
    const existing = mount()
    getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css', 'legal/terms.md'] })
    await existing.loadFiles()

    await existing.handleNewLegal()
    expect(postAddLegalTerms).not.toHaveBeenCalled()

    getProjectFiles.mockResolvedValue({ files: ['index.yml'] })
    const fresh = mount()
    await fresh.loadFiles()
    fresh.currentFileName.value = 'index.yml'

    getProjectFiles.mockResolvedValue({ files: ['index.yml', 'legal/terms.md'] })
    await fresh.handleNewLegal()
    expect(postAddLegalTerms).toHaveBeenCalledWith('proj')
    expect(fresh.currentFileName.value).toBe('legal/terms.md')
  })

  it('selecting "index.css" creates it empty the first time, and is a no-op once it already exists', async () => {
    getProjectFiles.mockResolvedValue({ files: ['index.yml'] })
    const s = mount()
    await s.loadFiles()
    s.currentFileName.value = 'index.yml'

    getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css'] })
    await s.selectFile('index.css')

    expect(putProjectFile).toHaveBeenCalledWith('proj', 'index.css', '')
    expect(s.currentFileName.value).toBe('index.css')

    putProjectFile.mockClear()
    s.currentFileName.value = 'index.yml'
    await s.selectFile('index.css')
    expect(putProjectFile).not.toHaveBeenCalled()
  })

  describe('handleDeleteFile', () => {
    it('never deletes index.yml, skips the confirm for a media asset, and honours a declined confirm', async () => {
      const s = mount()

      await s.handleDeleteFile('index.yml')
      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteProjectFile).not.toHaveBeenCalled()

      await s.handleDeleteFile('media/logo.png')
      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteProjectFile).toHaveBeenCalledWith('proj', 'media/logo.png')

      await s.handleDeleteFile('media/title.mp3')
      expect(confirmDialog).not.toHaveBeenCalled()
      expect(deleteProjectFile).toHaveBeenCalledWith('proj', 'media/title.mp3')

      confirmDialog.mockResolvedValue(false)
      await s.handleDeleteFile('behaviour/notes.md')
      expect(deleteProjectFile).not.toHaveBeenCalledWith('proj', 'behaviour/notes.md')
    })

    it('switches back to index.yml only when the open file went away, cascaded theme assets included', async () => {
      confirmDialog.mockResolvedValue(true)

      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'behaviour/other.md'] })
      const other = mount()
      other.currentFileName.value = 'behaviour/other.md'
      await other.handleDeleteFile('behaviour/notes.md')
      expect(deleteProjectFile).toHaveBeenCalledWith('proj', 'behaviour/notes.md')
      expect(other.currentFileName.value).toBe('behaviour/other.md')
      other.unmount?.()

      const open = mount()
      open.currentFileName.value = 'behaviour/notes.md'
      await open.handleDeleteFile('behaviour/notes.md')
      expect(open.currentFileName.value).toBe('index.yml')
      open.unmount?.()

      getProjectFiles.mockResolvedValue({ files: ['index.yml', 'index.css', 'media/logo.png'] })
      const cascaded = mount()
      await cascaded.loadFiles()
      cascaded.currentFileName.value = 'media/logo.png'
      getProjectFiles.mockResolvedValue({ files: ['index.yml'] })
      await cascaded.handleDeleteFile('index.css')
      expect(cascaded.currentFileName.value).toBe('index.yml')
    })
  })

  it('handleFileSaved emits "saved"', () => {
    const s = mount()
    s.handleFileSaved()
    expect(emit).toHaveBeenCalledWith('saved')
  })

  describe('jumpToDefinition', () => {
    const indexYml = 'states:\n  greeting:\n    contextual-prompt: hi\n'

    it('switches to index.yml first when another file is open, unless asked to stay silent', async () => {
      const s = mount()
      s.currentFileName.value = 'index.css'
      setEditor(s, { isDirty: false })
      s.designPanelRef.value.indexYmlEditorRef.content = indexYml
      s.designPanelRef.value.indexYmlEditorRef.jumpToLine = vi.fn()

      await s.jumpToDefinition({ kind: 'state', stateKey: 'greeting' })

      expect(s.currentFileName.value).toBe('index.yml')
      expect(s.designPanelRef.value.indexYmlEditorRef.jumpToLine).toHaveBeenCalledWith(1)

      s.currentFileName.value = 'index.css'
      await s.jumpToDefinition({ kind: 'state', stateKey: 'greeting' }, { silent: true })
      expect(s.currentFileName.value).toBe('index.css')
    })

    it('jumps directly when index.yml is already open, and does nothing when the target cannot be located', async () => {
      const s = mount()
      s.currentFileName.value = 'index.yml'
      const jumpToLine = vi.fn()
      s.designPanelRef.value = { indexYmlEditorRef: { content: indexYml, jumpToLine, isDirty: false } }

      await s.jumpToDefinition({ kind: 'state', stateKey: 'greeting' })
      expect(jumpToLine).toHaveBeenCalledWith(1)

      jumpToLine.mockClear()
      await s.jumpToDefinition({ kind: 'state', stateKey: 'nope' })
      expect(jumpToLine).not.toHaveBeenCalled()
    })
  })
})
