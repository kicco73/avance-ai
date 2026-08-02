<script setup>
import { computed, nextTick, ref } from 'vue'
import { clearEnv, deleteEnvValue, getEnv, putEnvValue } from '../../api.js'

const props = defineProps({
  // See InspectorMetricsTab.vue's own untilMessageId — when set, this
  // tab shows a historical point-in-time snapshot (see backend's
  // ChatService.get_env), which is always read-only: edits only ever
  // apply going forward from "now", there's no "editing history".
  untilMessageId: { type: [Number, String], default: null }
})

const envLoading = ref(false)
const stored = ref({})
const computedValues = ref({})
const isLive = computed(() => props.untilMessageId == null)

// Stored entries are free-form and editable/deletable; computed ones
// (see backend's ENV_COMPUTED_KEYS) never are — reported separately by
// the backend for exactly this reason, not merged (see
// backend/src/chat/env.py's Env.to_dict vs stored/computed).
const storedEntries = computed(() => Object.entries(stored.value))
const computedEntries = computed(() => Object.entries(computedValues.value))

async function loadEnv() {
  envLoading.value = true
  try {
    const result = await getEnv(props.untilMessageId ?? undefined)
    stored.value = result.stored
    computedValues.value = result.computed
  } catch {
    // already surfaced via apiFetch
  } finally {
    envLoading.value = false
  }
}

// Which stored key is currently being edited inline, if any — a plain
// text input replaces its value display while this is set.
const editingKey = ref(null)
const editingValue = ref('')
// A plain (non-ref-array) element ref: `ref="editInputRef"` inside the
// v-for below would make Vue collect it into an array regardless of how
// many rows actually render one at a time (only the row currently being
// edited does, via v-if) — this function-ref form assigns the single
// live element directly instead.
let editInputEl = null
function setEditInputRef(el) {
  editInputEl = el
}

async function startEditing(key) {
  if (!isLive.value) return
  editingKey.value = key
  editingValue.value = stored.value[key]
  await nextTick()
  editInputEl?.focus()
  editInputEl?.select()
}

function cancelEditing() {
  editingKey.value = null
}

async function commitEditing() {
  const key = editingKey.value
  if (key === null) return
  editingKey.value = null
  const value = editingValue.value
  if (value === stored.value[key]) return // unchanged — skip the round trip
  try {
    const result = await putEnvValue(key, value)
    stored.value = result.stored
    computedValues.value = result.computed
  } catch {
    // already surfaced via apiFetch
  }
}

async function removeKey(key) {
  try {
    const result = await deleteEnvValue(key)
    stored.value = result.stored
    computedValues.value = result.computed
  } catch {
    // already surfaced via apiFetch
  }
}

async function clearAll() {
  if (!window.confirm('Clear all stored environment values? This cannot be undone.')) return
  try {
    const result = await clearEnv()
    stored.value = result.stored
    computedValues.value = result.computed
  } catch {
    // already surfaced via apiFetch
  }
}

defineExpose({ loadEnv })
</script>

<template>
  <div class="inspector-env-section">
    <p v-if="envLoading" class="signals-status">Loading…</p>
    <template v-else>
      <div class="inspector-signal-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-env">AI</span>
          <span class="inspector-signal-name">Environment memory</span>
          <button
            v-if="isLive && storedEntries.length"
            class="inspector-env-clear-btn"
            title="Clear all stored values"
            @click="clearAll"
          >Clear all</button>
        </div>
        <p v-if="!storedEntries.length" class="inspector-env-empty">No stored values yet.</p>
        <div v-for="[key, value] in storedEntries" :key="key" class="inspector-env-row">
          <strong class="inspector-env-key">{{ key }}:</strong>
          <input
            v-if="editingKey === key"
            :ref="setEditInputRef"
            v-model="editingValue"
            class="inspector-env-input"
            @keydown.enter="commitEditing"
            @keydown.esc="cancelEditing"
            @blur="commitEditing"
          />
          <span
            v-else
            class="inspector-env-value"
            :class="{ 'inspector-env-value-editable': isLive }"
            :title="isLive ? 'Click to edit' : 'Read-only (viewing history)'"
            @click="startEditing(key)"
          >{{ value === null ? '—' : value }}</span>
          <button
            v-if="isLive"
            class="inspector-env-delete-btn"
            title="Delete this key"
            @click="removeKey(key)"
          >×</button>
        </div>
      </div>

      <div class="inspector-signal-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-env-computed">Computed</span>
          <span class="inspector-signal-name">Always up to date</span>
        </div>
        <p v-for="[key, value] in computedEntries" :key="key" class="inspector-detail-field">
          <strong>{{ key }}:</strong> {{ value === null ? '—' : value }}
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.inspector-env-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0.6rem; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-env { background: #8a5a44; }
.inspector-detail-badge-env-computed { background: #6b7280; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-detail-field { margin: 0 0 0.3rem; line-height: 1.4; font-size: 0.8rem; color: #444; word-break: break-word; }
.inspector-env-empty { margin: 0; font-size: 0.8rem; color: #888; font-style: italic; }
.inspector-env-row { display: flex; align-items: baseline; gap: 0.4rem; font-size: 0.8rem; color: #444; padding: 0.15rem 0; }
.inspector-env-key { flex-shrink: 0; }
.inspector-env-value { flex: 1; word-break: break-word; border-radius: 4px; padding: 0.05rem 0.3rem; margin: -0.05rem -0.3rem; }
.inspector-env-value-editable { cursor: text; }
.inspector-env-value-editable:hover { background: #eef2f9; }
.inspector-env-input { flex: 1; font: inherit; color: inherit; border: 1px solid #4a6fa5; border-radius: 4px; padding: 0.05rem 0.3rem; background: white; min-width: 0; }
.inspector-env-delete-btn { flex-shrink: 0; width: 1.1rem; height: 1.1rem; line-height: 1; border: none; border-radius: 50%; background: none; color: #999; cursor: pointer; font-size: 0.85rem; padding: 0; }
.inspector-env-delete-btn:hover { background: #fdecea; color: #c62828; }
.inspector-env-clear-btn { margin-left: auto; flex-shrink: 0; border: 1px solid #c62828; border-radius: 6px; background: white; color: #c62828; cursor: pointer; font-size: 0.7rem; padding: 0.15rem 0.5rem; }
.inspector-env-clear-btn:hover { background: #c62828; color: white; }
</style>
