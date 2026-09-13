import { ref } from 'vue'

export const needsLogin = ref(false)

export function requireLogin() {
  needsLogin.value = true
}

export function clearLoginRequirement() {
  needsLogin.value = false
}
