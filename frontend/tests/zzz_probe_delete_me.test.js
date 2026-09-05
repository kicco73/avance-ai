import { describe, it } from 'vitest'
import { writeFileSync } from 'fs'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'

describe('probe', () => {
  it('renders yaml with highlighting classes', () => {
    const doc = 'states:\n  intake:\n    ui-label: Intake\n    contextual-prompt: |\n      Greet the user.\n    chat: true\n    # a comment\n    actions:\n      - name: go\n        target: other\n'
    const host = document.createElement('div')
    document.body.appendChild(host)
    const view = new EditorView({ doc, extensions: [basicSetup, yaml()], parent: host })
    writeFileSync('/tmp/probe_output.html', host.innerHTML)
    view.destroy()
  })
})
