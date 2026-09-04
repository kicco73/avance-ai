// Minimal CodeMirror 6 language mode for CSV — a source's own sources/<id>.csv
// content has no real "syntax" beyond quoted fields and delimiters, so this
// is a small hand-written StreamLanguage rather than a whole @codemirror/lang-*
// package (none exists for CSV). Highlighted via CodeEditor.vue's own
// basicSetup (already wires syntaxHighlighting(defaultHighlightStyle)) —
// 'string'/'separator'/'number' are legacy CodeMirror token-style names
// StreamLanguage maps to real highlight tags automatically.
import { StreamLanguage } from '@codemirror/language'

const NUMBER_RE = /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/

function readField(stream) {
  let field = ''
  while (!stream.eol() && stream.peek() !== ',') {
    field += stream.next()
  }
  return field
}

export const csvLanguage = StreamLanguage.define({
  token(stream) {
    if (stream.eat(',')) return 'separator'
    if (stream.peek() === '"') {
      stream.next()
      while (!stream.eol()) {
        if (stream.next() === '"') {
          if (stream.peek() === '"') stream.next() // escaped "" inside a quoted field
          else break
        }
      }
      return 'string'
    }
    const field = readField(stream)
    return NUMBER_RE.test(field) ? 'number' : null
  }
})

export function csv() {
  return csvLanguage
}
