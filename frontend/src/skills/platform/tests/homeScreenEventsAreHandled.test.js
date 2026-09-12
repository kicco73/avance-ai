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

// A key of a listener map, in every shape one is written: `name: handler`
// and `'name': handler`, one per line or several on one (see App.vue's
// own profileMenuListeners, which is a single line).
function handledNames(source) {
  return new Set([...source.matchAll(/[{,\n]\s*'?([a-z-]+)'?:\s*[A-Za-z(]/g)].map((m) => m[1]))
}

describe('the admin home screen', () => {
  // The one that mounts it, and the only one that hears it: an event
  // raised here reaches AdminHome's own listener map and nowhere else.
  // A handler in App.vue means nothing unless AdminHome passes it up —
  // which is exactly how `about` stayed broken after being "fixed" in
  // the shell.
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
