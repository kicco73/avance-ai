import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import CodeEditor from '../CodeEditor.vue'

vi.mock('../../../api.js', () => ({
  getProjectFile: vi.fn().mockResolvedValue({
    content: 'ui-label: Intake\n', can_undo: false, can_redo: false,
    content_type: 'text/yaml', media_type: 'text/yaml',
  }),
  putProjectFile: vi.fn(),
  undoProjectFile: vi.fn(),
  redoProjectFile: vi.fn()
}))

describe('CodeEditor.vue colors index.yml plain scalar values, not just keys', () => {
  let container

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  it('wraps both the key and its plain-scalar value in a highlighted span', async () => {
    const app = createApp(CodeEditor, { projectId: 'proj', fileName: 'index.yml' })
    const instance = app.mount(container)
    await vi.waitFor(() => expect(instance.loading).toBe(false))

    const spans = Array.from(container.querySelectorAll('.cm-content span'))
    const keySpan = spans.find((el) => el.textContent === 'ui-label')
    const valueSpan = spans.find((el) => el.textContent === 'Intake')

    expect(keySpan).not.toBeUndefined()
    expect(valueSpan).not.toBeUndefined()
    expect(keySpan.className).not.toBe('')
    expect(valueSpan.className).not.toBe('')

    app.unmount()
  })
})
