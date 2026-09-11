import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { dirname, resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Every relative import points at a file that exists.
//
// The suite cannot see this on its own: a test only loads what it
// imports, so a component nobody tests can name a module that is not
// there and everything stays green. Only `vite build` notices, and a
// build is not something anyone remembers to run before pushing — master
// went three commits without compiling because src/mic.js moved out from
// under ChatView.vue, and earlier a rename of the /api/chat route
// rewrote '../../api/chat.js' into a module that was never created.
//
// Both were one-line breakages of the import graph, and both cost more
// to find than this costs to run.

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SCANNED = ['src', 'tests']
const SOURCE = /\.(js|vue)$/
// `from './x'`, `import './x'` and `import('./x')` alike — anything
// quoted that starts with a relative segment.
const RELATIVE_IMPORT = /(?:from|import)\s*\(?\s*['"](\.[^'"]*)['"]/g
// A specifier may leave off what Vite fills in.
const CANDIDATES = ['', '.js', '.vue', '/index.js', '/index.vue']

function sourceFiles(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return entry === 'node_modules' ? [] : sourceFiles(path)
    return SOURCE.test(entry) ? [path] : []
  })
}

function resolves(fromFile, specifier) {
  const target = resolve(dirname(fromFile), specifier)
  return CANDIDATES.some((suffix) => existsSync(target + suffix))
}

function danglingImports() {
  return SCANNED.flatMap((area) => sourceFiles(join(ROOT, area))).flatMap((file) =>
    [...readFileSync(file, 'utf8').matchAll(RELATIVE_IMPORT)]
      .filter(([, specifier]) => !resolves(file, specifier))
      .map(([, specifier]) => `${file.slice(ROOT.length + 1)} → ${specifier}`)
  )
}

describe('the import graph', () => {
  it('never names a file that is not there', () => {
    expect(danglingImports()).toEqual([])
  })

  it('was actually scanned', () => {
    // A pattern that matches nothing would pass the check above for
    // free, so this fails if the scan stops finding imports at all.
    const scanned = SCANNED.flatMap((area) => sourceFiles(join(ROOT, area)))
    expect(scanned.length).toBeGreaterThan(100)
  })
})
