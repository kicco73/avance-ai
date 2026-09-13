import { apiFetch } from '../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function postBuildLocalModule(projectId) {
  return apiFetch(`${API_URL}/skills/build/projects/${encodeURIComponent(projectId)}/local-module`, { method: 'POST' })
}

export function getBuildRequirements(projectId) {
  return apiFetch(`${API_URL}/skills/build/projects/${encodeURIComponent(projectId)}/requirements`)
}

export function postBuildBackendCopy(projectId, excludedSkills = [], onProgress) {
  return apiFetch(`${API_URL}/skills/build/projects/${encodeURIComponent(projectId)}/backend-copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ excluded_skills: excludedSkills })
  }, { parse: 'sse', onProgress })
}
