// An event a screen declares and nobody listens to is a button that does
// nothing, and it fails silently: `about` was dropped from App.vue's own
// listeners in a refactor and the "About Avance" logo stopped opening
// anything, with no error anywhere.
//
// Read off the source rather than by mounting: this is about the wiring
// between three files, not about what any of them renders.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(process.cwd(), 'src')

function read(path) {
  return readFileSync(join(SRC, path), 'utf8')
}

// `defineEmits([...])`, as a screen declares what it can raise.
function declaredEmits(source) {
  const call = source.match(/defineEmits\(\[([\s\S]*?)\]\)/)
  if (!call) return []
  return [...call[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
}

// A key of a listener map, in either shape a map uses: `name: handler`
// and `'name': handler`.
function handledNames(source) {
  return new Set([...source.matchAll(/^\s*'?([a-z-]+)'?:\s*[A-Za-z(]/gm)].map((m) => m[1]))
}

describe('the admin home screen', () => {
  it('has something listening for every event it declares', () => {
    const raised = declaredEmits(read('skills/platform/components/settings/ManageProjectsView.vue'))
    expect(raised.length).toBeGreaterThan(0)

    // Either the home that mounts it answers, or it forwards to the shell.
    const handled = new Set([
      ...handledNames(read('skills/platform/components/AdminHome.vue')),
      ...handledNames(read('App.vue')),
    ])

    expect(raised.filter((name) => !handled.has(name))).toEqual([])
  })
})
