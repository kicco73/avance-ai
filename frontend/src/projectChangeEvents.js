// Two facts, two registries. `projectChanged` is one project's own
// content moving (an edit landed); `projectsChanged` is the catalog
// itself moving (a project created, deleted, renamed, published,
// activated). Whoever displays either observes it here instead of being
// told by whoever caused it.
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
