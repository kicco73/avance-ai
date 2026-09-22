import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'


export function getAbout() {
  return apiFetch(`${API_URL}/core/settings/about`)
}





export function getServicesConfig() {
  return apiFetch(`${API_URL}/core/settings/services`)
}

export function getAiUsage() {
  return apiFetch(`${API_URL}/core/settings/services/ai-usage`)
}

export function getDbUsage() {
  return apiFetch(`${API_URL}/core/settings/services/db-usage`)
}

export function getScheduledTasks(status, order = 'asc') {
  const params = new URLSearchParams({ status, order })
  return apiFetch(`${API_URL}/core/settings/tasks?${params}`)
}
