import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// The Build view's Target step, "Local module": compiles the project's
// published revision into a package under the backend's own src/build/.
// The other targets in that step aren't wired to anything yet.
export function postBuildLocalModule(projectId) {
  return apiFetch(`${API_URL}/skills/build/projects/${encodeURIComponent(projectId)}/local-module`, { method: 'POST' })
}

// What this backend has installed and a build may leave out, plus the
// packages this project's own automaton makes mandatory (`required`).
// Both are read on the server: the list is whatever is actually in its
// source tree, and the rule that task.send_mail needs mail lives in
// mail, never here.
export function getBuildRequirements(projectId) {
  return apiFetch(`${API_URL}/skills/build/projects/${encodeURIComponent(projectId)}/requirements`)
}

// `excludedSkills` names the packages this build leaves out. Sending the
// excluded ones rather than the included ones means a client that does
// not know about a skill can never drop it by accident.
//
// A backend copy is a job of several steps ending in the built backend's
// own test run, so this streams rather than waits: `onProgress` gets each
// chunk the job broadcasts, whose `result` carries the step table (see
// build/build_job.py). What resolves is the last chunk's report.
export function postBuildBackendCopy(projectId, excludedSkills = [], onProgress) {
  return apiFetch(`${API_URL}/skills/build/projects/${encodeURIComponent(projectId)}/backend-copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ excluded_skills: excludedSkills })
  }, { parse: 'sse', onProgress })
}
