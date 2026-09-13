import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(process.cwd(), 'src')

function read(path) {
  return readFileSync(join(SRC, path), 'utf8')
}

function declaredEmits(source) {
  const call = source.match(/defineEmits\(\[([\s\S]*?)\]\)/)
  if (!call) return []
  return [...call[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
}

function handledNames(source) {
  return new Set([...source.matchAll(/[{,\n]\s*'?([a-z-]+)'?:\s*[A-Za-z(]/g)].map((m) => m[1]))
}

describe('the admin home screen', () => {
  it('has AdminHome listening for every event it declares', () => {
    const raised = declaredEmits(read('skills/platform/components/settings/ManageProjectsView.vue'))
    expect(raised.length).toBeGreaterThan(0)

    const handled = handledNames(read('skills/platform/components/AdminHome.vue'))

    expect(raised.filter((name) => !handled.has(name))).toEqual([])
  })

  it('has the shell listening for everything AdminHome passes up', () => {
    const passedUp = declaredEmits(read('skills/platform/components/AdminHome.vue'))
    expect(passedUp.length).toBeGreaterThan(0)

    const handled = handledNames(read('App.vue'))

    expect(passedUp.filter((name) => !handled.has(name))).toEqual([])
  })
})
