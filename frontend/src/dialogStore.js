import { markRaw, ref } from 'vue'

const queue = ref([])

export const activeDialog = ref(null)

let nextId = 0

function enqueue(request) {
  return new Promise((resolve) => {
    queue.value.push({ ...request, id: ++nextId, resolve })
    if (!activeDialog.value) activeDialog.value = queue.value[0]
  })
}

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

export function promptDialog({ title, body, placeholder = '', initialValue = '', validate, okLabel = 'OK' }) {
  return enqueue({ kind: 'prompt', title, body, placeholder, initialValue, validate, okLabel })
}

export function textareaDialog({ title, body, placeholder = '', initialValue = '', validate, okLabel = 'OK' }) {
  return enqueue({ kind: 'textarea', title, body, placeholder, initialValue, validate, okLabel })
}

export function chooseDialog({ title, body, options }) {
  return enqueue({ kind: 'choose', title, body, options })
}

export function infoDialog({ title = '', body, okLabel = null, markdown = false }) {
  return enqueue({ kind: 'info', title, body, okLabel, markdown })
}

export function aboutDialog({ version }) {
  return enqueue({ kind: 'about', version })
}

export function customDialog({ component, props = {}, wide = false }) {
  return enqueue({ kind: 'custom', component: markRaw(component), props, wide })
}

export function blockingDialog({ component, props = {}, wide = false }) {
  return enqueue({ kind: 'blocking', component: markRaw(component), props, wide })
}
