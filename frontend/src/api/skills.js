import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getSkills() {
  return apiFetch(`${API_URL}/skills`)
}
