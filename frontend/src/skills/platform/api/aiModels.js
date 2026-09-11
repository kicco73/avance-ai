import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// IMPORTANT — platform's routes, called from a core file. Choosing which
// model answers is an operation somebody performs from a panel, so the
// backend routes moved to avance_platform/deployment_controller.py; this
// file cannot follow until aiModelStore.js and ModelMenu.vue do, and they
// are wired into the core chat header rather than contributed to it.

export function getAiModels() {
  return apiFetch(`${API_URL}/skills/platform/ai/models`)
}

export function postAiModelSelection(index) {
  return apiFetch(`${API_URL}/skills/platform/ai/models/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index })
  })
}

export function getTestChatModels() {
  return apiFetch(`${API_URL}/skills/platform/ai/models/test`)
}

export function postTestChatModelSelection(index) {
  return apiFetch(`${API_URL}/skills/platform/ai/models/test/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index })
  })
}
