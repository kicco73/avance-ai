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
          if (stream.peek() === '"') stream.next()
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
