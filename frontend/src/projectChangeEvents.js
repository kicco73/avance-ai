const listeners = new Set()

export function onProjectChanged(handler) {
  listeners.add(handler)
  return () => listeners.delete(handler)
}

export async function emitProjectChanged(projectName) {
  await Promise.all([...listeners].map((handler) => handler(projectName)))
}
