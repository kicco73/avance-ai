<script setup>
// Appended after an action's own InspectorDetailCard while its edit form
// is open (see InspectorStateTab.vue) — one block per env: entry, same
// block/header shape as InspectorEnvKeysTab.vue's own env-key blocks. The
// header field here is a <select> over already-declared project env keys
// rather than free text: an action can only ever assign a value to a key
// the project has already declared, never invent a new one.
import { ref, watch } from 'vue'
import CardMenu from './CardMenu.vue'
import TriggerEditor from './TriggerEditor.vue'

const props = defineProps({
  env: { type: Object, default: () => ({}) },
  keyOptions: { type: Array, default: () => [] } // string[] — every valid <select> choice
})
const emit = defineEmits(['set-field'])

// Rows are id-keyed, not key-text-keyed: while editing, two rows can
// transiently share a key (or have none yet) — a plain object couldn't
// represent that, and v-for still needs a stable identity either way.
let nextId = 0
function rowsFromMap(map) {
  return Object.entries(map || {}).map(([key, value]) => ({ id: nextId++, key, value }))
}
function mapFromRows(list) {
  const map = {}
  for (const row of list) if (row.key) map[row.key] = row.value
  return map
}

const rows = ref(rowsFromMap(props.env))

// An external change (a different action's env prop) resyncs `rows`.
// Skipped when it's just this component's own commit() echoing back
// through the parent — same "already matches, nothing to do" guard
// TriggerEditor's own model watch uses — so committing one row never
// remounts every other row's own CodeMirror instance along with it.
watch(() => props.env, (newValue) => {
  if (JSON.stringify(newValue ?? {}) === JSON.stringify(mapFromRows(rows.value))) return
  rows.value = rowsFromMap(newValue)
})

function commit() {
  emit('set-field', 'env', mapFromRows(rows.value))
}

function addRow() {
  // Not committed — no key chosen yet, nothing valid to save until one is.
  rows.value = [...rows.value, { id: nextId++, key: '', value: '' }]
}

function removeRow(id) {
  rows.value = rows.value.filter((row) => row.id !== id)
  commit()
}

// A row's own current key stays selectable even after it drops out of
// keyOptions (the project's env schema changed elsewhere) — otherwise the
// <select> would silently jump to a different key and corrupt this row.
function optionsFor(row) {
  return row.key && !props.keyOptions.includes(row.key) ? [row.key, ...props.keyOptions] : props.keyOptions
}

function handleValueBlur(row) {
  if (!row.key) return // no key chosen yet — nothing meaningful to save
  commit()
}
</script>

<template>
  <div class="inspector-signal-list action-env-list">
    <div v-for="row in rows" :key="row.id" class="inspector-signal-block">
      <div class="inspector-signal-header">
        <span class="inspector-detail-badge inspector-detail-badge-env">Env</span>
        <select v-model="row.key" class="inspector-signal-label-input action-env-key-select" @click.stop @change="commit">
          <option value="" disabled>Select key…</option>
          <option v-for="key in optionsFor(row)" :key="key" :value="key">{{ key }}</option>
        </select>
        <CardMenu>
          <button type="button" class="card-menu-item-danger" @click="removeRow(row.id)">Delete</button>
        </CardMenu>
      </div>
      <label class="inspector-signal-form-label" title="A Python expression, evaluated server-side">
        <span class="inspector-py-field-icon" title="Python expression">PY</span>
        Value
      </label>
      <TriggerEditor v-model="row.value" @click.stop @blur="handleValueBlur(row)" />
    </div>
    <button type="button" class="inspector-signals-add-btn" @click="addRow">+ Add env key</button>
  </div>
</template>

<style scoped>
.action-env-list { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.75rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-env { background: #00838f; }
.inspector-signal-label-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; }
.action-env-key-select { cursor: pointer; }
.action-env-key-select:hover, .action-env-key-select:focus { border-color: #ccc; background: white; }
.inspector-signal-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 0.5rem 0 0.15rem; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.inspector-py-field-icon { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 1.1rem; height: 0.85rem; border-radius: 3px; background: #4b8bbe; color: white; font-size: 0.55rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -0.02em; }
.inspector-signals-add-btn { flex-shrink: 0; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-signals-add-btn:hover { background: #eef2f9; }
</style>
