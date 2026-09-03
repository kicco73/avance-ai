<script setup>
// InspectorDetailCard.vue's own "On enter" badge opens this instead of
// the old inline editor — same TriggerEditor, just given real room
// (large) for a multi-line actuator script. Committed via its own Save
// button, not blur — the field is now big/complex enough that merely
// clicking around inside the editor itself (selecting text, scrolling,
// dismissing a completion popup) could plausibly blur it without the
// user meaning to confirm anything. `onCommit` (InspectorDetailCard.vue's
// own saveField prop, a real awaited call — never the fire-and-forget
// set-field emit every other field here uses) resolves true only once
// the write has actually landed server-side; a malformed script fails
// validation there; Save waits for that answer and keeps the dialog open
// on failure instead of closing over an edit that never saved (the
// error itself surfaces the usual way, via the global ErrorBanner).
// Clear (see clearValue below) only ever touches the editor's own local
// text — no commit, no close — so the user can still back out of it by
// closing without saving.
// `closeDialog` (DialogHost.vue's own provide) is what actually closes
// this dialog on success — a 'custom' dialogStore.js dialog has no
// action row/close mechanism of its own otherwise.
import { inject, ref } from 'vue'
import TriggerEditor from './TriggerEditor.vue'

const props = defineProps({
  initialValue: { type: String, default: '' },
  excludeNamespaces: { type: Array, default: () => [] },
  onCommit: { type: Function, required: true }
})

const value = ref(props.initialValue)
// Own root element, handed to TriggerEditor as its tooltipParent — this
// component is rendered inside DialogHost.vue's native <dialog>
// (showModal(), the browser's own top layer), so completion/hover
// tooltips must mount somewhere inside that same dialog, not <body>
// (TriggerEditor's own default), or they'd render invisibly behind it.
const rootEl = ref(null)

const closeDialog = inject('closeDialog')
const saving = ref(false)

async function confirmAndClose() {
  if (value.value === props.initialValue) {
    closeDialog()
    return
  }
  saving.value = true
  try {
    if (await props.onCommit(value.value)) closeDialog()
  } finally {
    saving.value = false
  }
}

// Local only — clears the editor's own text, nothing else: no commit,
// no close. The user still has to press Save (or close without saving)
// afterward, same as clearing it by hand and hitting Save would do.
function clearValue() {
  value.value = ''
}

// Ctrl+S or Option/Alt+S saves — bound on the dialog's own root (not
// TriggerEditor, which is a generic, reusable editor with no "save"
// concept of its own) so it fires the same way whether focus is inside
// the CodeMirror editor or elsewhere in the dialog: a plain keydown
// listener catches it either way since nothing in TriggerEditor's own
// keymap binds this key, so the browser event just bubbles up here
// unclaimed. preventDefault stops the browser's own native "Save Page"
// first. `code`, not `key`: Option turns 's' into 'ß' on a Mac keyboard
// (key reports the character it would type, code the physical key), so
// `key` alone would silently never match Option+S there.
function handleKeydown(event) {
  if (event.code !== 'KeyS' || !(event.ctrlKey || event.altKey)) return
  event.preventDefault()
  if (!saving.value) confirmAndClose()
}
</script>

<template>
  <div class="on-enter-dialog" ref="rootEl" @keydown="handleKeydown">
    <h2 class="on-enter-dialog-title">On enter</h2>
    <p class="on-enter-dialog-hint">Script executed when running the action and before landing to next state.</p>
    <TriggerEditor v-model="value" :exclude-namespaces="excludeNamespaces" :tooltip-parent="rootEl" large />
    <div class="on-enter-dialog-actions">
      <button type="button" class="on-enter-dialog-clear-btn" @click="clearValue">
        Clear
      </button>
      <button type="button" class="on-enter-dialog-ok-btn" :disabled="saving" @click="confirmAndClose">
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.on-enter-dialog {
  position: relative;
  width: 100%;
}

.on-enter-dialog-title {
  margin: 0 0 0.3rem;
  padding-right: 1.6rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.on-enter-dialog-hint {
  margin: 0 0 0.6rem;
  font-size: 0.8rem;
  color: #777;
}

.on-enter-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1.1rem;
}

.on-enter-dialog-ok-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
  font-size: 0.85rem;
  cursor: pointer;
}

.on-enter-dialog-ok-btn:hover:not(:disabled) {
  background: #3d5c8a;
}

.on-enter-dialog-ok-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.on-enter-dialog-clear-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #555;
  font-size: 0.85rem;
  cursor: pointer;
}

.on-enter-dialog-clear-btn:hover {
  background: #f0f0f0;
}
</style>
