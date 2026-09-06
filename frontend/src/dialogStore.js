import { markRaw, ref } from 'vue'

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
export function promptDialog({ title, body, placeholder = '', initialValue = '', validate, okLabel = 'OK' }) {
  return enqueue({ kind: 'prompt', title, body, placeholder, initialValue, validate, okLabel })
}

// Same contract as promptDialog (resolves the entered text, or null on
// Cancel/×/ESC/backdrop) but for free-form multi-line text — DialogHost.vue
// renders a <textarea> instead of a single-line <input>, so Enter inserts
// a newline rather than submitting.
export function textareaDialog({ title, body, placeholder = '', initialValue = '', validate, okLabel = 'OK' }) {
  return enqueue({ kind: 'textarea', title, body, placeholder, initialValue, validate, okLabel })
}

// options: [{ id, label, danger? }] — one button per option, resolving
// its id, plus a Cancel resolving null.
export function chooseDialog({ title, body, options }) {
  return enqueue({ kind: 'choose', title, body, options })
}

// Purely informational — nothing to decide. `okLabel`, when given, adds
// a single labeled button (e.g. "Bye!") alongside the usual × close
// button; omitted (the default), the × is the only way to close it.
export function infoDialog({ title = '', body, okLabel = null, markdown = false }) {
  return enqueue({ kind: 'info', title, body, okLabel, markdown })
}

// SettingsMenu's "About Avance...": just the logo and the version, no
// separate title/body text.
export function aboutDialog({ version }) {
  return enqueue({ kind: 'about', version })
}

// A caller-supplied component, rendered inside the same shared chrome
// (backdrop, card, enter/leave transition, the × close button) as every
// other kind here — for content too specific to fit confirm/prompt/
// choose/info/about's own fixed shapes (e.g. ShareProjectDialog.vue's
// QR code). `props` are bound onto it as-is. Resolves once the dialog
// closes, same promise contract as every other kind, though most custom
// content has nothing meaningful to resolve with — the × button alone
// is enough to close it. `wide`: the usual 420px card is cramped for
// something like a real code editor (see OnEnterDialog.vue) — opts into
// a roomier max-width instead (see DialogHost.vue's own .app-dialog-wide).
export function customDialog({ component, props = {}, wide = false }) {
  // markRaw: `component` is a static component definition, not app
  // state — pushing it into queue.value (a ref) unmarked would let Vue's
  // reactivity wrap it in a Proxy, which <component :is="..."> in
  // DialogHost.vue doesn't expect to receive.
  return enqueue({ kind: 'custom', component: markRaw(component), props, wide })
}
