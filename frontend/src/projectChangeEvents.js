const projectListeners = new Set()
const catalogListeners = new Set()

export function onProjectChanged(handler) {
  projectListeners.add(handler)
  return () => projectListeners.delete(handler)
}

export async function emitProjectChanged(projectId) {
  await Promise.all([...projectListeners].map((handler) => handler(projectId)))
}

export function onProjectsChanged(handler) {
  catalogListeners.add(handler)
  return () => catalogListeners.delete(handler)
}

export async function emitProjectsChanged() {
  await Promise.all([...catalogListeners].map((handler) => handler()))
}
