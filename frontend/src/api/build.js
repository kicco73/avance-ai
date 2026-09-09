import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The Build view's Target step, "Local module": compiles the project's
// published revision into a package under the backend's own src/build/.
// The other targets in that step aren't wired to anything yet.
export function postBuildLocalModule(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/build/local-module`, { method: 'POST' })
}

export function postBuildBackendCopy(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/build/backend-copy`, { method: 'POST' })
}
