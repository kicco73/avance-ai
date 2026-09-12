// What the conversation on screen can reach (see backend docs/BUS.md's
// own ui.services), and whoever wants to know.
//
// Deliberately imports nothing: the chat store publishes here and the
// skills listen here, and both of those already import each other's
// world — going through the skill registry instead would close the
// circle and leave the module graph half-initialised.
const listeners = new Set()

export function onServices(handler) {
  listeners.add(handler)
  return () => listeners.delete(handler)
}

export function publishServices(available) {
  // One listener that throws must not take the others with it — the same
  // tolerance the boot state gets in useAppBoot.js.
  for (const handler of [...listeners]) {
    try {
      handler(available)
    } catch (err) {
      console.error('a skill failed to read what this conversation reaches', err)
    }
  }
}
