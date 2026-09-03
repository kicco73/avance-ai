import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'
import { infoDialog } from './dialogStore.js'

function show(body_md) {
  infoDialog({ body: body_md, markdown: true })
}

// The full set of functions an action's "on-enter" script can call — the
// one module every such local function lives in, so a new local only
// ever needs adding here.
export const onEnterLocals = { celebrate, notify, show }

// Runs `script` (e.g. "celebrate()") with each of onEnterLocals bound as
// a top-level identifier — `new Function` compiles a fresh function body
// each call, so `script` never inherits this module's real local bindings.
export function runOnEnterScript(script) {
  if (!script) return
  try {
    const names = Object.keys(onEnterLocals)
    const values = names.map((name) => onEnterLocals[name])
    const run = new Function(...names, script)
    run(...values)
  } catch (err) {
    console.error('on-enter script failed:', script, err)
  }
}
