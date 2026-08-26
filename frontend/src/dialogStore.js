import { ref } from 'vue'

// At most one dialog is ever shown. A request made while one's already
// open waits here instead of stacking visually or replacing it — DialogHost.vue
// only ever renders queue[0] (see activeDialog below).
const queue = ref([])

// What DialogHost.vue actually renders — kept as its own ref so the host
// only has to react to this single slot going null <-> non-null, never
// touching the queue itself.
export const activeDialog = ref(null)

let nextId = 0

function enqueue(request) {
  return new Promise((resolve) => {
    queue.value.push({ ...request, id: ++nextId, resolve })
    if (!activeDialog.value) activeDialog.value = queue.value[0]
  })
}

// Called by DialogHost.vue once its own close animation and the native
// <dialog>'s close() have both actually finished — not on click, so the
// promise never settles before the exit transition the caller might be
// watching (e.g. a v-if straight off the resolved value) has played out.
export function resolveActiveDialog(value) {
  const current = activeDialog.value
  if (!current) return
  queue.value.shift()
  activeDialog.value = queue.value[0] ?? null
  current.resolve(value)
}

export function confirmDialog({ title, body, okLabel = 'Confirm', danger = false }) {
  return enqueue({ kind: 'confirm', title, body, okLabel, danger })
}

// validate(value), if given, runs on every keystroke — a returned
// non-empty string is shown inline under the field and blocks Confirm;
// undefined/null/'' means valid.
export function promptDialog({ title, body, placeholder = '', initialValue = '', validate }) {
  return enqueue({ kind: 'prompt', title, body, placeholder, initialValue, validate })
}

// options: [{ id, label, danger? }] — one button per option, resolving
// its id, plus a Cancel resolving null.
export function chooseDialog({ title, body, options }) {
  return enqueue({ kind: 'choose', title, body, options })
}

// Purely informational — a single Close button, nothing to decide.
export function infoDialog({ title, body }) {
  return enqueue({ kind: 'info', title, body })
}

// SettingsMenu's "About Avance...": just the logo and the version, no
// separate title/body text.
export function aboutDialog({ version }) {
  return enqueue({ kind: 'about', version })
}
