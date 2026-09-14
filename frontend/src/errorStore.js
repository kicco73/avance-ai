import { computed, ref } from 'vue'

const message = ref('')
const detail = ref('')
const severity = ref('error')
const raisedOn = ref('')

export const currentScreen = ref('')

const onThisScreen = computed(() => raisedOn.value === currentScreen.value)

export const errorMessage = computed(() => (onThisScreen.value ? message.value : ''))
export const errorDetail = computed(() => (onThisScreen.value ? detail.value : ''))
export const errorSeverity = computed(() => (onThisScreen.value ? severity.value : 'error'))

export function setApiError(text, moreDetail = '', screen = currentScreen.value) {
  message.value = text
  detail.value = moreDetail || ''
  severity.value = 'error'
  raisedOn.value = screen
}

export function setApiWarning(text, moreDetail = '', screen = currentScreen.value) {
  message.value = text
  detail.value = moreDetail || ''
  severity.value = 'warning'
  raisedOn.value = screen
}

export function clearApiError() {
  message.value = ''
  detail.value = ''
  severity.value = 'error'
  raisedOn.value = currentScreen.value
}

export function enterScreen(screen) {
  currentScreen.value = screen
  clearApiError()
}
