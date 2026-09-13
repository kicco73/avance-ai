const listeners = new Set()

export function onServices(handler) {
  listeners.add(handler)
  return () => listeners.delete(handler)
}

export function publishServices(available) {
  for (const handler of [...listeners]) {
    try {
      handler(available)
    } catch (err) {
      console.error('a skill failed to read what this conversation reaches', err)
    }
  }
}
