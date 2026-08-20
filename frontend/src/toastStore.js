import { ref } from 'vue'

// Toasts fired by an action's on-enter script `notify` local — a plain
// queue any component can render from, since more than one can be in
// flight at once.
export const toasts = ref([])

let nextId = 0
const AUTO_DISMISS_MS = 6000

export function dismissToast(id) {
  const idx = toasts.value.findIndex((t) => t.id === id)
  if (idx !== -1) toasts.value.splice(idx, 1)
}

// The on-enter script's `notify(title, body)`. `body` is markdown,
// rendered by ToastContainer.vue — never here, so this store stays free
// of any HTML/sanitization concern.
export function notify(title, body) {
  const id = ++nextId
  toasts.value.push({ id, title, body })
  setTimeout(() => dismissToast(id), AUTO_DISMISS_MS)
}
