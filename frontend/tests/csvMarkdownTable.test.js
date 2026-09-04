import { describe, expect, it } from 'vitest'
import { csvToMarkdownTable } from '../src/csvMarkdownTable.js'

describe('csvToMarkdownTable', () => {
  it('renders header and rows as a markdown table', () => {
    const result = csvToMarkdownTable('city,country\nParis,France\nBerlin,Germany\n')

    expect(result).toBe(
      '| city | country |\n' +
      '| --- | --- |\n' +
      '| Paris | France |\n' +
      '| Berlin | Germany |'
    )
  })

  it('renders an empty placeholder for empty content', () => {
    expect(csvToMarkdownTable('')).toBe('*(empty)*')
  })

  it('renders just the header row when there is no data', () => {
    expect(csvToMarkdownTable('city,country\n')).toBe('| city | country |\n| --- | --- |')
  })

  it('pads ragged rows to the widest row', () => {
    expect(csvToMarkdownTable('a,b,c\n1,2\n')).toBe('| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |  |')
  })

  it('escapes pipe characters in cells', () => {
    expect(csvToMarkdownTable('note\na|b\n')).toContain('a\\|b')
  })

  it('parses quoted fields with embedded commas as one cell', () => {
    expect(csvToMarkdownTable('name,note\n"Doe, John",hi\n')).toContain('| Doe, John | hi |')
  })

  it('unescapes doubled quotes inside a quoted field', () => {
    expect(csvToMarkdownTable('note\n"say ""hi"""\n')).toContain('say "hi"')
  })
})
