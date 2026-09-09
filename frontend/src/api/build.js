import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The Build view's Target step, "Local module": compiles the project's
// published revision into a package under the backend's own src/build/.
// The other targets in that step aren't wired to anything yet.
export function postBuildLocalModule(projectId) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/build/local-module`, { method: 'POST' })
}

// What this backend has installed and a build may leave out. Read from
// the source tree on the server, so the list is whatever is actually
// there rather than something the frontend keeps in sync.
export function getBuildSkills() {
  return apiFetch(`${API_URL}/build/skills`)
}

// `excludedSkills` names the packages this build leaves out. Sending the
// excluded ones rather than the included ones means a client that does
// not know about a skill can never drop it by accident.
export function postBuildBackendCopy(projectId, excludedSkills = []) {
  return apiFetch(`${API_URL}/projects/${encodeURIComponent(projectId)}/build/backend-copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ excluded_skills: excludedSkills })
  })
}
