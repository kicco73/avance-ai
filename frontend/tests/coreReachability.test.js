import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')
const SKILLS = join(SRC, 'skills')

// What the app is entered through, plus the tests: a module nothing
// imports — not the app, not even a test — is dead weight that a build
// still copies and hands to a customer. Anything loaded by name rather
// than by import would have to be listed here; nothing is, today.
function skillManifests() {
  if (!statSync(SKILLS).isDirectory()) return []
  return readdirSync(SKILLS)
    .filter((entry) => statSync(join(SKILLS, entry)).isDirectory())
    .map((entry) => join(SKILLS, entry, 'index.js'))
}

// main.js is what the browser loads; every skill manifest is what the
// registry's glob loads without naming it. A core module that only a
// skill imports is reached through the second, which is why they are
// roots here and not something the walk discovers.
const ROOTS = [join(SRC, 'main.js'), ...skillManifests()]

function filesUnder(directory, match) {
  const found = []
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) found.push(...filesUnder(path, match))
    else if (match.test(entry)) found.push(path)
  }
  return found
}

// vi.mock is deliberately not an import here: mocking a module says
// nothing about anything using it, and a mock left behind by a refactor
// would otherwise keep a dead module looking alive.
function importsOf(path) {
  const text = readFileSync(path, 'utf8')
  const found = []
  const patterns = [
    /from\s+['"]([^'"]+)['"]/g,
    /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    /(?:^|\n)\s*import\s+['"]([^'"]+)['"]/g,
  ]
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) found.push(match[1])
  }
  return found
}

function reachable(roots) {
  const seen = new Set()
  const queue = [...roots]
  while (queue.length) {
    const path = queue.pop()
    if (seen.has(path)) continue
    seen.add(path)
    let specifiers
    try {
      specifiers = importsOf(path)
    } catch {
      continue
    }
    for (const specifier of specifiers) {
      if (!specifier.startsWith('.')) continue
      queue.push(resolve(dirname(path), specifier))
    }
  }
  return seen
}

describe('every core module is reachable', () => {
  it('has no module nothing imports, from the app or from a test', () => {
    const roots = [...ROOTS, ...filesUnder(join(ROOT, 'tests'), /\.js$/)]
    const reached = reachable(roots)
    const orphans = filesUnder(SRC, /\.(js|vue)$/)
      .filter((path) => !path.startsWith(SKILLS + '/'))
      .filter((path) => !reached.has(path))
      .map((path) => relative(ROOT, path))

    expect(orphans).toEqual([])
  })

  it('reaches a skill only through the registry, and the registry only through the app', () => {
    const fromTheApp = reachable([join(SRC, 'main.js')])

    expect(fromTheApp.has(join(SKILLS, 'registry.js'))).toBe(true)
    expect([...fromTheApp].filter((path) => /\/skills\/[^/]+\//.test(path))).toEqual([])
  })

  it('mocks a module that exists', () => {
    // A vi.mock left pointing at a module a refactor moved is silent:
    // vitest is happy to mock a path nothing imports, and the test goes
    // on passing while the thing it meant to replace is somewhere else.
    const dangling = filesUnder(join(ROOT, 'tests'), /\.js$/)
      .flatMap((path) => {
        const text = readFileSync(path, 'utf8')
        return [...text.matchAll(/vi\.mock\s*\(\s*['"](\.[^'"]+)['"]/g)]
          .map((match) => ({ path, specifier: match[1] }))
      })
      .filter(({ path, specifier }) => !existsSync(resolve(dirname(path), specifier)))
      .map(({ path, specifier }) => `${relative(ROOT, path)} -> ${specifier}`)

    expect(dangling).toEqual([])
  })
})
