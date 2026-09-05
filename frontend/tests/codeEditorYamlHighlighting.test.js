// Regression: @lezer/yaml tags a plain scalar (an unquoted value, or a
// `|`/`>` block's own body) as tags.content — left uncolored by
// @codemirror/language's defaultHighlightStyle, so most of index.yml's
// actual text (ui-labels, contextual-prompts, on-enter bodies) used to
// render with no syntax highlighting at all, only keys/comments/block
// markers colored. CodeEditor.vue now also registers a value-highlight
// style for text/yaml buffers — this pins that both keys AND plain
// values end up in a highlighted <span>, and that registering it doesn't
// silently drop the default style (a real CodeMirror pitfall: a second
// {fallback: true} highlighter is discarded outright, dropping keys'
// own color, unless defaultHighlightStyle is also re-registered as a
// regular, non-fallback style alongside it — see CodeEditor.vue's own
// yamlValueHighlightStyle comment).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/api.js', () => ({
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
    vi.resetModules()
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  it('wraps both the key and its plain-scalar value in a highlighted span', async () => {
    const CodeEditor = (await import('../src/components/CodeEditor.vue')).default
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
