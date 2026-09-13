import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import CodeEditor from '../CodeEditor.vue'
import * as api from '../../../api.js'

vi.mock('../../../api.js', () => ({
  getProjectFile: vi.fn().mockResolvedValue({ content: 'a: 1\n', can_undo: false, can_redo: false }),
  putProjectFile: vi.fn(),
  undoProjectFile: vi.fn(),
  redoProjectFile: vi.fn()
}))

function buildError(fields) {
  const err = new Error('bad build')
  err.fields = fields
  return err
}

describe('CodeEditor.vue save() build-error handling', () => {
  let container

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  async function mountEditor(props) {
    const buildErrorCalls = []
    const app = createApp(CodeEditor, { ...props, onBuildError: (line) => buildErrorCalls.push(line) })
    const instance = app.mount(container)
    await vi.waitFor(() => expect(instance.loading).toBe(false))
    return { instance, buildErrorCalls, app }
  }

  it('emits build-error with the line when the fields match this exact project/file/revision', async () => {
    api.putProjectFile.mockRejectedValue(buildError({
      project_id: 'proj', file: 'index.yml', line: 7, revision: 3
    }))
    const { instance, buildErrorCalls } = await mountEditor({ projectId: 'proj', fileName: 'index.yml', currentRevision: 3 })

    await instance.save()

    expect(buildErrorCalls).toEqual([7])
  })

  it('does not emit build-error when the revision is stale', async () => {
    api.putProjectFile.mockRejectedValue(buildError({
      project_id: 'proj', file: 'index.yml', line: 7, revision: 3
    }))
    const { instance, buildErrorCalls } = await mountEditor({ projectId: 'proj', fileName: 'index.yml', currentRevision: 4 })

    await instance.save()

    expect(buildErrorCalls).toEqual([])
  })

  it('does not emit build-error when the fields name a different file', async () => {
    api.putProjectFile.mockRejectedValue(buildError({
      project_id: 'proj', file: 'index.css', line: 7, revision: 3
    }))
    const { instance, buildErrorCalls } = await mountEditor({ projectId: 'proj', fileName: 'index.yml', currentRevision: 3 })

    await instance.save()

    expect(buildErrorCalls).toEqual([])
  })

  it('does not emit build-error for a plain error with no fields', async () => {
    api.putProjectFile.mockRejectedValue(new Error('network error'))
    const { instance, buildErrorCalls } = await mountEditor({ projectId: 'proj', fileName: 'index.yml', currentRevision: 3 })

    await instance.save()

    expect(buildErrorCalls).toEqual([])
  })
})
