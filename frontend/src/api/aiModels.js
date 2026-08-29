import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getAiModels() {
  return apiFetch(`${API_URL}/ai/models`)
}

export function postAiModelSelection(index) {
  return apiFetch(`${API_URL}/ai/models/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index })
  })
}

export function getTestChatModels() {
  return apiFetch(`${API_URL}/ai/models/test`)
}

export function postTestChatModelSelection(index) {
  return apiFetch(`${API_URL}/ai/models/test/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index })
  })
}
