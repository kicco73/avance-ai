// The tool-call trace's own result rendering (see MessageBubble.vue): the
// raw CSV a source driver returns becomes a markdown table, and anything
// that isn't tabular falls back to being shown raw (empty string here).
import { describe, expect, it } from 'vitest'
import { csvToMarkdownTable } from '../src/toolResultTable.js'

describe('csvToMarkdownTable', () => {
  it('turns a header plus rows into a markdown table', () => {
    expect(csvToMarkdownTable('city,country\nParis,France\nBerlin,Germany\n')).toBe(
      '| city | country |\n| --- | --- |\n| Paris | France |\n| Berlin | Germany |'
    )
  })

  it('squares short rows off against the header and escapes pipes and newlines', () => {
    expect(csvToMarkdownTable('a,b,c\n"x|y","two\nlines"')).toBe(
      '| a | b | c |\n| --- | --- | --- |\n| x\\|y | two lines |  |'
    )
  })

  it('returns nothing for an error, an empty result, a single line, or a single column', () => {
    expect(csvToMarkdownTable('error: no such column')).toBe('')
    expect(csvToMarkdownTable('')).toBe('')
    expect(csvToMarkdownTable(null)).toBe('')
    expect(csvToMarkdownTable('city,country\n')).toBe('')
    expect(csvToMarkdownTable('city\nParis\n')).toBe('')
  })
})
