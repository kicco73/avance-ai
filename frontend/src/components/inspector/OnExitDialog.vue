<script setup>
// InspectorDetailCard.vue's own "On exit" badge opens this — same shape
// as OnEnterDialog.vue (large TriggerEditor, its own Save/Clear, Ctrl+S/
// Option+S, awaited onCommit through saveField so a malformed script
// keeps the dialog open on failure) but for `on-exit`: one `env.key = expr`
// env-write line per non-blank line, no `actuator` namespace — that
// stays on-enter's own job (see automaton.eval_action_on_exit /
// AutomatonValidator.validate_on_exit on the backend). The future
// replacement for the per-row Env editor this dialog's own badge used to
// sit beside (see ActionEnvEditor.vue, now removed).
import { inject, ref } from 'vue'
import TriggerEditor from './TriggerEditor.vue'

const props = defineProps({
  initialValue: { type: String, default: '' },
  excludeNamespaces: { type: Array, default: () => [] },
  onCommit: { type: Function, required: true }
})

const value = ref(props.initialValue)
// Own root element, handed to TriggerEditor as its tooltipParent — same
// reasoning as OnEnterDialog.vue's own rootEl (this dialog renders
// inside DialogHost.vue's native <dialog>, so completion/hover tooltips
// must mount inside it too, not <body>).
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

// Local only — see OnEnterDialog.vue's own clearValue.
function clearValue() {
  value.value = ''
}

// Ctrl+S / Option+S saves — see OnEnterDialog.vue's own handleKeydown
// for why `code` rather than `key` (Option turns 's' into 'ß' on a Mac).
function handleKeydown(event) {
  if (event.code !== 'KeyS' || !(event.ctrlKey || event.altKey)) return
  event.preventDefault()
  if (!saving.value) confirmAndClose()
}
</script>

<template>
  <div class="on-exit-dialog" ref="rootEl" @keydown="handleKeydown">
    <h2 class="on-exit-dialog-title">On exit</h2>
    <p class="on-exit-dialog-hint">One "env.key = expression" write per line, evaluated when the action fires and before landing to next state.</p>
    <TriggerEditor v-model="value" :exclude-namespaces="excludeNamespaces" :tooltip-parent="rootEl" large />
    <div class="on-exit-dialog-actions">
      <button type="button" class="on-exit-dialog-clear-btn" @click="clearValue">
        Clear
      </button>
      <button type="button" class="on-exit-dialog-ok-btn" :disabled="saving" @click="confirmAndClose">
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.on-exit-dialog {
  position: relative;
  width: 100%;
}

.on-exit-dialog-title {
  margin: 0 0 0.3rem;
  padding-right: 1.6rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.on-exit-dialog-hint {
  margin: 0 0 0.6rem;
  font-size: 0.8rem;
  color: #777;
}

.on-exit-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1.1rem;
}

.on-exit-dialog-ok-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
  font-size: 0.85rem;
  cursor: pointer;
}

.on-exit-dialog-ok-btn:hover:not(:disabled) {
  background: #3d5c8a;
}

.on-exit-dialog-ok-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.on-exit-dialog-clear-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #555;
  font-size: 0.85rem;
  cursor: pointer;
}

.on-exit-dialog-clear-btn:hover {
  background: #f0f0f0;
}
</style>
