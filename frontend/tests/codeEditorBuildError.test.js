// CodeEditor.vue's own save() — on a failed save whose error carries
// AutomatonBuildError fields naming exactly this project/file/revision,
// it emits 'build-error' with the line to jump to; any mismatch (wrong
// project/file, or a stale revision — another save/publish/revert
// landed since) must suppress it instead.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/api.js', () => ({
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
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    api = await import('../src/api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  // No @vue/test-utils in this project — mounted as the app root directly,
  // same as chatWindowSessionActions.test.js's own ChatView.vue mount.
  // Vue 3's own onXxx-prop-as-listener convention catches 'build-error'
  // without needing a wrapping parent component.
  async function mountEditor(props) {
    const CodeEditor = (await import('../src/components/CodeEditor.vue')).default
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
