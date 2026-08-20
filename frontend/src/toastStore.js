import { ref } from 'vue'

// Toasts fired by an action's own on-enter script (see onEnterActions.js's
// `notify` local) — a plain queue any component can render from, same
// shape as errorStore.js's own single-slot store, just a list instead of
// one value since more than one can be in flight at once.
export const toasts = ref([])

let nextId = 0
const AUTO_DISMISS_MS = 6000

export function dismissToast(id) {
  const idx = toasts.value.findIndex((t) => t.id === id)
  if (idx !== -1) toasts.value.splice(idx, 1)
}

// The on-enter script's own `notify(title, body)` (see onEnterActions.js) —
// `body` is markdown, rendered by ToastContainer.vue the same way a chat
// message is (see markdown.js's renderMarkdown) — never here, so this
// store stays free of any HTML/sanitization concern.
export function notify(title, body) {
  const id = ++nextId
  toasts.value.push({ id, title, body })
  setTimeout(() => dismissToast(id), AUTO_DISMISS_MS)
}
