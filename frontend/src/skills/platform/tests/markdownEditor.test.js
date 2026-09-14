import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import MarkdownEditor from '../MarkdownEditor.vue'
import * as api from '../api.js'

vi.mock('../api.js', () => ({
  getProjectFile: vi.fn(),
  putProjectFile: vi.fn(),
  undoProjectFile: vi.fn(),
  redoProjectFile: vi.fn()
}))

describe('MarkdownEditor.vue', () => {
  let container

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    api.getProjectFile.mockResolvedValue({ content: '# Title\n\nHello *world*\n', can_undo: true, can_redo: false })
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  async function mountEditor(listeners = {}) {
    const app = createApp(MarkdownEditor, { projectId: 'proj', fileName: 'behaviour/notes.md', ...listeners })
    const instance = app.mount(container)
    await vi.waitFor(() => expect(instance.loading).toBe(false))
    return { instance, app }
  }

  it('renders the file as rich text and is clean after loading', async () => {
    const { instance } = await mountEditor()

    expect(container.querySelector('.milkdown h1')?.textContent).toBe('Title')
    expect(container.querySelector('.milkdown em')?.textContent).toBe('world')
    expect(instance.isDirty).toBe(false)
    expect(instance.canUndo).toBe(true)
    expect(instance.canRedo).toBe(false)
  })

  it('shows one bar holding the formatting commands and undo, redo, save', async () => {
    await mountEditor()

    const bar = container.querySelector('.milkdown .milkdown-top-bar')
    expect(bar.querySelector('.top-bar-heading-selector')).not.toBeNull()
    const titles = [...bar.querySelectorAll('button')].map((b) => b.title || b.textContent.trim())
    expect(titles).toEqual(expect.arrayContaining(['Undo', 'Redo', 'Save']))
    expect(bar.querySelector('button[title="Undo"]').disabled).toBe(false)
    expect(bar.querySelector('button[title="Redo"]').disabled).toBe(true)
  })

  it('edits made while a save is in flight stay dirty', async () => {
    let finish
    api.putProjectFile.mockImplementation((_p, _f, content) => new Promise((r) => { finish = () => r({ content, can_undo: true, can_redo: false }) }))
    const { instance } = await mountEditor()

    const pending = instance.save()
    await vi.waitFor(() => expect(finish).toBeDefined())
    instance.$.exposed.content.value = '# Title\n\nHello *world* again\n'
    finish()
    await pending

    expect(instance.isDirty).toBe(true)
  })

  it('keeps a single newline as a line break inside the paragraph, as the product renders it', async () => {
    api.getProjectFile.mockResolvedValue({ content: 'first line\nsecond line\n', can_undo: false, can_redo: false })
    const { instance } = await mountEditor()

    expect(container.querySelector('.milkdown p span[data-type="hardbreak"]')).not.toBeNull()
    expect(instance.content).toBe('first line\nsecond line\n')
  })

  it('stays clean when nobody edits, even after the editor settles a document ending in a list', async () => {
    api.getProjectFile.mockResolvedValue({ content: 'Rules:\n\n- one\n- two\n', can_undo: false, can_redo: false })
    const { instance } = await mountEditor()

    await new Promise((resolve) => setTimeout(resolve, 300))

    expect(instance.isDirty).toBe(false)
    expect(instance.content).toContain('- one')
  })

  it('saves the current markdown and becomes clean', async () => {
    api.putProjectFile.mockImplementation(async (_p, _f, content) => ({ content, can_undo: true, can_redo: false }))
    const saved = []
    const { instance } = await mountEditor({ onSaved: (r) => saved.push(r) })

    await instance.save()

    expect(api.putProjectFile).toHaveBeenCalledWith('proj', 'behaviour/notes.md', expect.stringContaining('# Title'))
    expect(saved).toHaveLength(1)
    expect(instance.isDirty).toBe(false)
  })

  it('undo replaces the document with the server revision', async () => {
    api.undoProjectFile.mockResolvedValue({ content: '# Older\n', can_undo: false, can_redo: true })
    const { instance } = await mountEditor()

    instance.undo()
    await vi.waitFor(() => expect(container.querySelector('.milkdown h1')?.textContent).toBe('Older'))

    expect(instance.canUndo).toBe(false)
    expect(instance.canRedo).toBe(true)
    expect(instance.content).toContain('# Older')
  })

  it('undo that renames the file emits renamed', async () => {
    api.undoProjectFile.mockResolvedValue({ renamed_to: 'behaviour/old.md' })
    const renamed = []
    const { instance } = await mountEditor({ onRenamed: (n) => renamed.push(n) })

    instance.undo()
    await vi.waitFor(() => expect(renamed).toEqual(['behaviour/old.md']))
  })
})
