import { ref } from 'vue'

// The single shared error state for the whole app: every REST failure
// (via api.js's apiFetch) and every websocket failure (App.vue's
// handleSocketMessage) writes here — nowhere else displays an error.
export const errorMessage = ref('')
export const errorDetail = ref('')
// 'error' (red, auto-dismisses after 10s — a transient failure the user
// doesn't need to act on) or 'warning' (amber, stays until the user
// closes it or the condition resolves itself — see ProjectService.
// recompute_availability's own AvailabilityChanged, which a paused
// project's own warning here reacts to). See ErrorBanner.vue's own
// styling/auto-dismiss logic, both keyed off this.
export const errorSeverity = ref('error')

export function setApiError(message, detail = '') {
  errorMessage.value = message
  errorDetail.value = detail || ''
  errorSeverity.value = 'error'
}

export function setApiWarning(message, detail = '') {
  errorMessage.value = message
  errorDetail.value = detail || ''
  errorSeverity.value = 'warning'
}

export function clearApiError() {
  errorMessage.value = ''
  errorDetail.value = ''
  errorSeverity.value = 'error'
}
