import { ref } from 'vue'

// The single shared error state for the whole app: every REST failure
// and every websocket failure writes here — nowhere else displays an error.
export const errorMessage = ref('')
export const errorDetail = ref('')
// 'error' (red, auto-dismisses after 10s) or 'warning' (amber, stays
// until the user closes it or the condition resolves itself). See
// ErrorBanner.vue's own styling/auto-dismiss logic, both keyed off this.
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
