import { describe, expect, it } from 'vitest'
import { summarizeImportFailures } from '../src/sessionImport.js'

function fileResult(name, ok, error = null) {
  return { file: { name }, ok, error }
}

describe('summarizeImportFailures', () => {
  it('returns null when every file imported successfully', () => {
    const results = [fileResult('a.txt', true), fileResult('b.txt', true)]
    expect(summarizeImportFailures(results)).toBeNull()
  })

  it('returns null for an empty batch (nothing failed because nothing ran)', () => {
    expect(summarizeImportFailures([])).toBeNull()
  })

  it('reports a mixed batch by count, listing only the failed files with their reasons', () => {
    const results = [
      fileResult('a.txt', true),
      fileResult('b.txt', false, 'Malformed transcript'),
      fileResult('c.txt', true)
    ]
    const summary = summarizeImportFailures(results)
    expect(summary.message).toBe('Imported 2 of 3 transcripts — 1 failed.')
    expect(summary.detail).toBe('b.txt: Malformed transcript')
  })

  it('reports every file failing as a distinct "all failed" message, not a 0-of-N count', () => {
    const results = [fileResult('a.txt', false, 'boom'), fileResult('b.txt', false, 'kaboom')]
    const summary = summarizeImportFailures(results)
    expect(summary.message).toBe('Failed to import all 2 transcripts.')
    expect(summary.detail).toBe('a.txt: boom\nb.txt: kaboom')
  })

  it('singularizes the "all failed" message for a single-file batch', () => {
    const results = [fileResult('a.txt', false, 'boom')]
    const summary = summarizeImportFailures(results)
    expect(summary.message).toBe('Failed to import the transcript.')
  })
})
