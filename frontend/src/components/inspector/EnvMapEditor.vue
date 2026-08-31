<script setup>
// A generic editor for a { key: expression } mapping — as many rows as
// the user wants, each [key text (datalist-suggested)][value expression]
// [remove]. `keyOptions` only suggests via the datalist; it never caps
// how many rows can exist — an unrecognized key is still just text here,
// left for the backend's own build-time check to accept or reject on
// commit. Reusable wherever that same shape shows up, not just an
// action's own `env:` field.
import { ref, watch } from 'vue'
import TriggerEditor from './TriggerEditor.vue'

const model = defineModel({ type: Object, default: () => ({}) })
const props = defineProps({
  keyOptions: { type: Array, default: () => [] } // string[], datalist suggestions only
})
const emit = defineEmits(['blur'])

// Rows are id-keyed, not key-text-keyed: while editing, two rows can
// transiently share a key (or have none yet) — a plain object couldn't
// represent that, and v-for still needs a stable identity either way.
// A module-level counter, not a per-instance one — guarantees a unique
// <datalist> id even with two EnvMapEditor instances mounted at once,
// which plain row ids (reset per component) couldn't.
let datalistIdCounter = 0
const datalistId = `env-map-editor-keys-${datalistIdCounter++}`

let nextId = 0
function rowsFromMap(map) {
  return Object.entries(map || {}).map(([key, value]) => ({ id: nextId++, key, value }))
}
function mapFromRows(list) {
  const map = {}
  for (const row of list) if (row.key) map[row.key] = row.value
  return map
}

const rows = ref(rowsFromMap(model.value))

// An external change (a different action selected, or the parent's own
// reset) resyncs `rows`. Skipped when it's just this component's own
// commit() echoing back through the parent's v-model — same "already
// matches, nothing to do" guard TriggerEditor's own model watch uses —
// so committing one row never remounts every other row's own CodeMirror
// instance along with it.
watch(model, (newValue) => {
  if (JSON.stringify(newValue ?? {}) === JSON.stringify(mapFromRows(rows.value))) return
  rows.value = rowsFromMap(newValue)
})

function commit() {
  model.value = mapFromRows(rows.value)
}

function addRow() {
  // Not committed — a blank key/expression isn't valid to save yet;
  // the row's own field blur commits once it has both.
  rows.value = [...rows.value, { id: nextId++, key: '', value: '' }]
}

function removeRow(id) {
  rows.value = rows.value.filter((row) => row.id !== id)
  commit()
  emit('blur')
}

function handleFieldBlur(row) {
  if (!row.key) return // still mid-entry — nothing meaningful to save yet
  commit()
  emit('blur')
}
</script>

<template>
  <div class="env-map-editor">
    <datalist :id="datalistId">
      <option v-for="option in keyOptions" :key="option" :value="option" />
    </datalist>
    <div v-for="row in rows" :key="row.id" class="env-map-editor-row">
      <input
        v-model="row.key"
        class="env-map-editor-key"
        :list="datalistId"
        placeholder="key"
        spellcheck="false"
        @click.stop
        @blur="handleFieldBlur(row)"
      />
      <TriggerEditor
        v-model="row.value"
        class="env-map-editor-value"
        @click.stop
        @blur="handleFieldBlur(row)"
      />
      <button type="button" class="env-map-editor-remove-btn" title="Remove" @click.stop="removeRow(row.id)">×</button>
    </div>
    <button type="button" class="env-map-editor-add-btn" title="Add an env assignment" @click.stop="addRow">+ Add</button>
  </div>
</template>

<style scoped>
.env-map-editor { display: flex; flex-direction: column; gap: 0.35rem; }
.env-map-editor-row { display: flex; align-items: flex-start; gap: 0.3rem; }
.env-map-editor-key { flex-shrink: 0; width: 40%; font: inherit; font-size: 0.78rem; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; padding: 0.3rem 0.25rem; border-radius: 6px; border: 1px solid #ccc; }
.env-map-editor-value { flex: 1; min-width: 0; }
.env-map-editor-remove-btn { flex-shrink: 0; width: 1.5rem; height: 1.5rem; line-height: 1; border: none; border-radius: 50%; background: none; color: #999; cursor: pointer; font-size: 0.95rem; padding: 0; margin-top: 0.15rem; }
.env-map-editor-remove-btn:hover { background: #fdecea; color: #c62828; }
.env-map-editor-add-btn { align-self: flex-start; margin-top: 0.1rem; padding: 0.3rem 0.6rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.78rem; cursor: pointer; }
.env-map-editor-add-btn:hover { background: #eef2f9; }
</style>
