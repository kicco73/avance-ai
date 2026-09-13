import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'
import { infoDialog } from './dialogStore.js'

function show(body_md) {
  infoDialog({ body: body_md, markdown: true })
}

export const taskLocals = { celebrate, notify, show }

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
