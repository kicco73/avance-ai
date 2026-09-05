<script setup>
import { computed, nextTick, ref } from 'vue'
import { clearEnv, deleteEnvValue, getEnv, putEnvValue } from '../../api.js'
import { confirmDialog } from '../../dialogStore.js'

const props = defineProps({
  // Whose env this tab shows/edits — every call below is scoped to it.
  // null (no session resolved yet) just shows the empty state.
  sessionId: { type: [Number, String], default: null },
  // When set, shows a historical point-in-time snapshot instead of live env.
  untilMessageId: { type: [Number, String], default: null },
  // Edits only ever apply going forward from "now", so this is true only
  // when untilMessageId is null (live) or pinned to the latest message
  // (still effectively "now").
  editable: { type: Boolean, default: true }
})

const envLoading = ref(false)
const memory = ref({})
const actionSet = ref({})
const aiAccess = ref({})
const isLive = computed(() => props.editable)

// Memory entries are the model's own free-form notes — editable; env
// (action-set) entries are the automaton's declared keys, written by an
// action's own env: or by the model's `update` tool — read-only here,
// each badged with its own ai-access (what the model may do with it).
const memoryEntries = computed(() => Object.entries(memory.value))
const envEntries = computed(() => Object.entries(actionSet.value))

function applyResult(result) {
  memory.value = result.memory
  actionSet.value = result.action_set
  aiAccess.value = result.ai_access ?? {}
}

function accessLabel(key) {
  return aiAccess.value[key] ?? 'none'
}

async function loadEnv() {
  if (props.sessionId == null) {
    memory.value = {}
    actionSet.value = {}
    aiAccess.value = {}
    return
  }
  envLoading.value = true
  try {
    applyResult(await getEnv(props.sessionId, props.untilMessageId ?? undefined))
  } catch {
    // already surfaced via apiFetch
  } finally {
    envLoading.value = false
  }
}

const editingKey = ref(null)
const editingValue = ref('')
// Function-ref instead of ref="editInputRef": inside the v-for below, a
// plain ref string would make Vue collect it into an array even though
// only one row ever renders the input at a time.
let editInputEl = null
function setEditInputRef(el) {
  editInputEl = el
}

async function startEditing(key) {
  if (!isLive.value) return
  editingKey.value = key
  editingValue.value = memory.value[key]
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
  if (value === memory.value[key]) return // unchanged — skip the round trip
  try {
    applyResult(await putEnvValue(props.sessionId, key, value))
  } catch {
    // already surfaced via apiFetch
  }
}

async function removeKey(key) {
  try {
    applyResult(await deleteEnvValue(props.sessionId, key))
  } catch {
    // already surfaced via apiFetch
  }
}

async function clearAll() {
  const ok = await confirmDialog({
    title: 'Clear memory',
    body: 'Clear every memory note and every env value? This cannot be undone.',
    okLabel: 'Clear',
    danger: true
  })
  if (!ok) return
  try {
    applyResult(await clearEnv(props.sessionId))
  } catch {
    // already surfaced via apiFetch
  }
}

// Always reloads regardless of whether this tab is active — cheap, and
// values can change while this tab isn't the one showing.
async function refresh() {
  await loadEnv()
}

defineExpose({ loadEnv, refresh })
</script>

<template>
  <div class="inspector-env-section">
    <p v-if="envLoading" class="signals-status">Loading…</p>
    <template v-else>
      <div class="inspector-signal-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-env-action">ENV</span>
          <span class="inspector-signal-name">Env</span>
        </div>
        <p v-if="!envEntries.length" class="inspector-env-empty">No values defined yet.</p>
        <div v-for="[key, value] in envEntries" :key="key" class="inspector-env-row">
          <strong class="inspector-env-key">{{ key }}:</strong>
          <span class="inspector-env-value">{{ value === null ? '—' : value }}</span>
          <span
            class="inspector-env-access"
            :class="'inspector-env-access-' + accessLabel(key)"
            :title="
              accessLabel(key) === 'readwrite'
                ? 'The model can read and update this key (ai-access: readwrite)'
                : accessLabel(key) === 'readonly'
                  ? 'The model can read this key (ai-access: readonly)'
                  : 'Scripts only — the model never sees this key (ai-access: none)'
            "
          >{{ accessLabel(key) }}</span>
        </div>
      </div>

      <div class="inspector-signal-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-env">AI</span>
          <span class="inspector-signal-name">Memory</span>
          <button
            v-if="isLive && memoryEntries.length"
            class="inspector-env-clear-btn"
            title="Clear all memory notes"
            @click="clearAll"
          >Clear all</button>
        </div>
        <p v-if="!memoryEntries.length" class="inspector-env-empty">No memory notes yet.</p>
        <div v-for="[key, value] in memoryEntries" :key="key" class="inspector-env-row">
          <span class="inspector-env-ai-icon" title="Read by the AI">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
          </span>
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
.inspector-detail-badge-env-action { background: #3d6b52; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-detail-field { margin: 0 0 0.3rem; line-height: 1.4; font-size: 0.8rem; color: #444; word-break: break-word; }
.inspector-env-empty { margin: 0; font-size: 0.8rem; color: #888; font-style: italic; }
.inspector-env-row { display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: #444; padding: 0.15rem 0; }
.inspector-env-ai-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; }
.inspector-env-key { flex-shrink: 0; }
.inspector-env-value { flex: 1; word-break: break-word; border-radius: 4px; padding: 0.05rem 0.3rem; margin: -0.05rem -0.3rem; }
.inspector-env-value-editable { cursor: text; }
.inspector-env-value-editable:hover { background: #eef2f9; }
.inspector-env-input { flex: 1; font: inherit; color: inherit; border: 1px solid #4a6fa5; border-radius: 4px; padding: 0.05rem 0.3rem; background: white; min-width: 0; }
.inspector-env-delete-btn { flex-shrink: 0; width: 1.1rem; height: 1.1rem; line-height: 1; border: none; border-radius: 50%; background: none; color: #999; cursor: pointer; font-size: 0.85rem; padding: 0; }
.inspector-env-delete-btn:hover { background: #fdecea; color: #c62828; }
.inspector-env-clear-btn { margin-left: auto; flex-shrink: 0; border: 1px solid #c62828; border-radius: 6px; background: white; color: #c62828; cursor: pointer; font-size: 0.7rem; padding: 0.15rem 0.5rem; }
.inspector-env-clear-btn:hover { background: #c62828; color: white; }
.inspector-env-access { flex-shrink: 0; font-size: 0.62rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.1rem 0.4rem; border-radius: 999px; color: white; background: #9e9e9e; }
.inspector-env-access-readonly { background: #4a6fa5; }
.inspector-env-access-readwrite { background: #6d28d9; }
</style>
