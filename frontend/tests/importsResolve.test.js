import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { dirname, resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SCANNED = ['src', 'tests']
const SOURCE = /\.(js|vue)$/
const RELATIVE_IMPORT = /(?:from|import)\s*\(?\s*['"](\.[^'"]*)['"]/g
const CANDIDATES = ['', '.js', '.vue', '/index.js', '/index.vue']
const LINE_COMMENT = /^\s*\/\/.*$/gm
const SELF = fileURLToPath(import.meta.url)

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
  return SCANNED.flatMap((area) => sourceFiles(join(ROOT, area)))
    .filter((file) => file !== SELF)
    .flatMap((file) =>
      [...readFileSync(file, 'utf8').replace(LINE_COMMENT, '').matchAll(RELATIVE_IMPORT)]
        .filter(([, specifier]) => !resolves(file, specifier))
        .map(([, specifier]) => `${file.slice(ROOT.length + 1)} → ${specifier}`)
    )
}

describe('the import graph', () => {
  it('never names a file that is not there', () => {
    expect(danglingImports()).toEqual([])
  })

  it('was actually scanned', () => {
    const scanned = SCANNED.flatMap((area) => sourceFiles(join(ROOT, area)))
    expect(scanned.length).toBeGreaterThan(100)
  })
})
