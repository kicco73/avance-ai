import { ref } from 'vue'

// The single shared error state for the whole app: every REST failure
// (via api.js's apiFetch) and every websocket failure (App.vue's
// handleSocketMessage) writes here — nowhere else displays an error.
export const errorMessage = ref('')
export const errorDetail = ref('')

export function setApiError(message, detail = '') {
  errorMessage.value = message
  errorDetail.value = detail || ''
}

export function clearApiError() {
  errorMessage.value = ''
  errorDetail.value = ''
}
