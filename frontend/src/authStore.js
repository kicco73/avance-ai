import { ref } from 'vue'

// Flips true the moment any API call comes back 401 — App.vue reads this
// to swap the whole UI for LoginView.vue, regardless of what else was
// showing (splash, chat, an overlay). Set from api.js's apiFetch, never
// from a component directly.
export const needsLogin = ref(false)

export function requireLogin() {
  needsLogin.value = true
}

export function clearLoginRequirement() {
  needsLogin.value = false
}
