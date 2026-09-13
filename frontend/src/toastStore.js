import { ref } from 'vue'

export const toasts = ref([])

let nextId = 0
const AUTO_DISMISS_MS = 6000

export function dismissToast(id) {
  const idx = toasts.value.findIndex((t) => t.id === id)
  if (idx !== -1) toasts.value.splice(idx, 1)
}

export function notify(title, body) {
  const id = ++nextId
  toasts.value.push({ id, title, body })
  setTimeout(() => dismissToast(id), AUTO_DISMISS_MS)
}
