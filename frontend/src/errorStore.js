import { ref } from 'vue'

export const errorMessage = ref('')
export const errorDetail = ref('')
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
