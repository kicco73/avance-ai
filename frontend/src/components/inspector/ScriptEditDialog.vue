<script setup>
// InspectorDetailCard.vue's own Trigger/On exit/Task badges all open this
// same dialog, just switched to a different starting tab — one CodeMirror
// editor per field, each with its own namespace exclusions, replacing the
// three separate surfaces (an always-inline TriggerEditor plus two
// standalone OnEnterDialog.vue/OnExitDialog.vue dialogs) that used to
// carry them. `showTrigger` is false for the init-action, which has no
// trigger of its own to edit.
//
// Save commits every tab whose buffer differs from what was last saved,
// each through its own awaited onCommit(field, value) call (InspectorDetailCard.vue's
// own saveField prop — a real awaited write, never the fire-and-forget
// set-field emit every other field here uses) so a malformed script
// leaves that field's own local edit in place and keeps the dialog open
// instead of closing over an edit that never saved (the error itself
// surfaces the usual way, via the global ErrorBanner). Clear only ever
// touches the active tab's own local text — no commit, no close — so the
// user can still back out of it by switching tabs or closing without saving.
import { computed, inject, ref } from 'vue'
import TriggerEditor from './TriggerEditor.vue'

const props = defineProps({
  initialTrigger: { type: String, default: '' },
  initialOnExit: { type: String, default: '' },
  initialTask: { type: String, default: '' },
  showTrigger: { type: Boolean, default: true },
  initialTab: { type: String, default: 'trigger' },
  onCommit: { type: Function, required: true }
})

const TAB_DEFS = [
  { key: 'trigger', label: 'Trigger', field: 'trigger', excludeNamespaces: ['task', 'chat'], hint: 'A Python expression, evaluated server-side, deciding whether this action is available.' },
  { key: 'on-exit', label: 'On Exit', field: 'on-exit', excludeNamespaces: ['task'], hint: 'One "env.key = expression" write per line, or a bare "chat.<method>(...)" call, evaluated when the action fires and before landing to next state.' },
  { key: 'task', label: 'Task', field: 'task', excludeNamespaces: ['session', 'chat'], hint: 'Script executed when running the action and before landing to next state.' }
]

const tabs = computed(() => TAB_DEFS.filter((t) => t.key !== 'trigger' || props.showTrigger))

const initialValues = { trigger: props.initialTrigger, 'on-exit': props.initialOnExit, task: props.initialTask }
const savedValues = ref({ ...initialValues })
const values = ref({ ...initialValues })

const startTab = tabs.value.some((t) => t.key === props.initialTab) ? props.initialTab : tabs.value[0].key
const activeTabKey = ref(startTab)
const activeTab = computed(() => tabs.value.find((t) => t.key === activeTabKey.value))

// Own root element, handed to TriggerEditor as its tooltipParent — this
// component is rendered inside DialogHost.vue's native <dialog>
// (showModal(), the browser's own top layer), so completion/hover
// tooltips must mount somewhere inside that same dialog, not <body>
// (TriggerEditor's own default), or they'd render invisibly behind it.
const rootEl = ref(null)

const closeDialog = inject('closeDialog')
const saving = ref(false)

function isDirty(tab) {
  return values.value[tab.field] !== savedValues.value[tab.field]
}

const anyDirty = computed(() => tabs.value.some(isDirty))

async function confirmAndClose() {
  const dirtyTabs = tabs.value.filter(isDirty)
  if (dirtyTabs.length === 0) {
    closeDialog()
    return
  }
  saving.value = true
  try {
    for (const tab of dirtyTabs) {
      if (!(await props.onCommit(tab.field, values.value[tab.field]))) return
      savedValues.value[tab.field] = values.value[tab.field]
    }
    closeDialog()
  } finally {
    saving.value = false
  }
}

// Local only — clears the active tab's own text, nothing else: no
// commit, no close. The user still has to press Save (or close without
// saving) afterward, same as clearing it by hand and hitting Save would do.
function clearActive() {
  values.value[activeTabKey.value] = ''
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
  <div class="script-edit-dialog" ref="rootEl" @keydown="handleKeydown">
    <div class="script-edit-dialog-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="script-edit-dialog-tab"
        :class="{ 'script-edit-dialog-tab-active': activeTabKey === tab.key, 'script-edit-dialog-tab-dirty': isDirty(tab) }"
        @click="activeTabKey = tab.key"
      >{{ tab.label }}</button>
    </div>
    <p class="script-edit-dialog-hint">{{ activeTab.hint }}</p>
    <TriggerEditor
      :key="activeTab.key"
      v-model="values[activeTab.field]"
      :exclude-namespaces="activeTab.excludeNamespaces"
      :tooltip-parent="rootEl"
      large
    />
    <div class="script-edit-dialog-actions">
      <button type="button" class="script-edit-dialog-clear-btn" @click="clearActive">
        Clear
      </button>
      <button type="button" class="script-edit-dialog-ok-btn" :disabled="saving || !anyDirty" @click="confirmAndClose">
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.script-edit-dialog {
  position: relative;
  width: 100%;
}

.script-edit-dialog-tabs {
  display: flex;
  gap: 0.3rem;
  margin: 0 0 0.5rem;
  padding-right: 1.6rem;
  border-bottom: 1px solid #eee;
}

.script-edit-dialog-tab {
  appearance: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin: 0 0 -1px;
  padding: 0.4rem 0.2rem 0.5rem;
  background: none;
  font: inherit;
  font-size: 0.9rem;
  font-weight: 600;
  color: #888;
  cursor: pointer;
}

.script-edit-dialog-tab:hover {
  color: #333;
}

.script-edit-dialog-tab-active {
  color: #333;
  border-bottom-color: #4a6fa5;
}

.script-edit-dialog-tab-dirty::after {
  content: '•';
  margin-left: 0.3rem;
  color: #4a6fa5;
}

.script-edit-dialog-hint {
  margin: 0 0 0.6rem;
  font-size: 0.8rem;
  color: #777;
}

.script-edit-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1.1rem;
}

.script-edit-dialog-ok-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
  font-size: 0.85rem;
  cursor: pointer;
}

.script-edit-dialog-ok-btn:hover:not(:disabled) {
  background: #3d5c8a;
}

.script-edit-dialog-ok-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.script-edit-dialog-clear-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #555;
  font-size: 0.85rem;
  cursor: pointer;
}

.script-edit-dialog-clear-btn:hover {
  background: #f0f0f0;
}
</style>
