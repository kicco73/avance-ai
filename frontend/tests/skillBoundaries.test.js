import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')
const SKILLS = join(SRC, 'skills')
const REGISTRY = join(SKILLS, 'registry.js')
const THIS_FILE = fileURLToPath(import.meta.url)

const KEY_HOMONYMS_IN_CORE = {
  whatsapp: ['src/components/ProfileView.vue']
}

function sourceFilesUnder(directory) {
  const found = []
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) {
      found.push(...sourceFilesUnder(path))
    } else if (/\.(js|vue)$/.test(entry)) {
      found.push(path)
    }
  }
  return found
}

function specifiersIn(path) {
  const text = readFileSync(path, 'utf8')
  const found = []
  for (const pattern of [/from\s+['"]([^'"]+)['"]/g, /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g, /vi\.mock\s*\(\s*['"]([^'"]+)['"]/g]) {
    for (const match of text.matchAll(pattern)) found.push(match[1])
  }
  return found
}

function skillTargetOf(path, specifier) {
  if (!specifier.startsWith('.')) return null
  const resolved = resolve(dirname(path), specifier)
  if (!resolved.startsWith(SKILLS + '/')) return null
  return relative(SKILLS, resolved).split('/')[0].replace(/\.js$/, '')
}

const skillKeys = readdirSync(SKILLS).filter((entry) => statSync(join(SKILLS, entry)).isDirectory())
const coreFiles = sourceFilesUnder(SRC).filter((path) => !path.startsWith(SKILLS + '/'))

describe('skill boundaries', () => {
  it('has no core file importing into a skill', () => {
    const offenders = coreFiles.flatMap((path) => specifiersIn(path)
      .filter((specifier) => skillTargetOf(path, specifier))
      .map((specifier) => `${relative(ROOT, path)} -> ${specifier}`))
    expect(offenders).toEqual([])
  })

  it('has no skill importing a sibling skill', () => {
    const offenders = []
    for (const key of skillKeys) {
      for (const path of sourceFilesUnder(join(SKILLS, key))) {
        for (const specifier of specifiersIn(path)) {
          const target = skillTargetOf(path, specifier)
          if (target && target !== key) offenders.push(`${relative(ROOT, path)} -> ${specifier}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('never names a skill key in a core file', () => {
    const offenders = []
    for (const key of skillKeys) {
      const allowed = KEY_HOMONYMS_IN_CORE[key] ?? []
      const traces = [`'${key}'`, `"${key}"`, `/api/skills/${key}/`, `skills/${key}`]
      for (const path of coreFiles) {
        const where = relative(ROOT, path)
        if (path === REGISTRY || allowed.includes(where)) continue
        const text = readFileSync(path, 'utf8')
        for (const trace of traces) {
          if (text.includes(trace)) offenders.push(`${where} mentions ${trace}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('names each skill after its own directory', () => {
    const offenders = []
    for (const key of skillKeys) {
      const manifest = join(SKILLS, key, 'index.js')
      let text
      try {
        text = readFileSync(manifest, 'utf8')
      } catch {
        offenders.push(`src/skills/${key} has no index.js`)
        continue
      }
      if (!new RegExp(`export const key = ['"]${key}['"]`).test(text)) {
        offenders.push(`src/skills/${key}/index.js does not declare key '${key}'`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('keeps every skill test inside its own skill', () => {
    const offenders = sourceFilesUnder(join(ROOT, 'tests'))
      .filter((path) => path !== THIS_FILE)
      .flatMap((path) => specifiersIn(path)
        .filter((specifier) => specifier.includes('/skills/'))
        .map((specifier) => `${relative(ROOT, path)} -> ${specifier}`))
    expect(offenders).toEqual([])
  })

  it('keeps the api barrel free of skill routes', () => {
    expect(readFileSync(join(SRC, 'api.js'), 'utf8')).not.toContain('skills/')
  })

  it('reaches a skill only through the registry glob', () => {
    const globs = readFileSync(REGISTRY, 'utf8').match(/import\.meta\.glob\(([^)]*)\)/g) ?? []
    expect(globs).toHaveLength(1)
    expect(specifiersIn(REGISTRY).filter((specifier) => specifier.includes('/'))).toEqual(['../skillRoster.js'])
  })
})
