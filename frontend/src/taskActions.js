import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'
import { infoDialog } from './dialogStore.js'

function show(body_md) {
  infoDialog({ body: body_md, markdown: true })
}

// The full set of functions a task or on-exit script's own wire-ready
// JS can call (celebrate()/notify(...)/show(...) — see chat.* on the
// backend) — the one module every such local function lives in, so a
// new local only ever needs adding here.
export const taskLocals = { celebrate, notify, show }

// Runs `script` (e.g. "celebrate()") with each of taskLocals bound as
// a top-level identifier — `new Function` compiles a fresh function body
// each call, so `script` never inherits this module's real local bindings.
export function runTaskScript(script) {
  if (!script) return
  try {
    const names = Object.keys(taskLocals)
    const values = names.map((name) => taskLocals[name])
    const run = new Function(...names, script)
    run(...values)
  } catch (err) {
    console.error('task script failed:', script, err)
  }
}
