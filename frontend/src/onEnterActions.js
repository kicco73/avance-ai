import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'

// The full set of functions an action's own "on-enter" script (see
// backend's automaton.Action.on_enter, sent over the wire as "on-enter")
// can call — the one module every such local function lives in, so
// runOnEnterScript below has exactly one place to bind names from, and a
// new local (like `notify`) only ever needs adding here.
export const onEnterLocals = { celebrate, notify }

// Runs `script` — the fired action's own on-enter field, verbatim from the
// YAML (e.g. "celebrate()" or "notify('Nice!', 'You reached **state
// B**.')") — with each of onEnterLocals bound as its own top-level
// identifier and nothing else from this module's real scope reachable
// through closure: `new Function` compiles a fresh top-level function body
// each call, so `script` only ever inherits globals, never local bindings
// like `celebrate`/`notify`'s own imports above — a script typo or a
// reference to some unrelated identifier fails loudly inside its own
// call, never silently touching this file's actual state.
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
