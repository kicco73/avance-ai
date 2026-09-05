<script setup>
// Schema-editing tab for the project-level `env:` section: create, rename,
// or delete a declared env key, editing its ui-description, default
// `value` — a Python expression, edited via TriggerEditor.vue like a
// trigger — and what the model may do with it: `ai-access` (none/readonly/
// readwrite, through an avance:env source) plus the `ai-definition` text
// the model reads about it, required whenever ai-access isn't none.
import { computed, nextTick, ref, watch, onMounted } from 'vue'
import { getProjectEnvKeys } from '../../api.js'
import { vAutosize } from './textareaAutosize.js'
import CardMenu from './CardMenu.vue'
import TriggerEditor from './TriggerEditor.vue'
import { handleEnterNext } from './enterToNextField.js'

const props = defineProps({
  projectId: { type: String, required: true },
  // 'env-key:<name>' for the entry a "+ Add env key" click just created;
  // null otherwise.
  recentlyAddedKey: { type: String, default: null }
})

const emit = defineEmits(['add-env-key', 'set-field', 'delete', 'jump-to-definition'])

function handleDeleteEnvKey(name) {
  emit('delete', name)
}

const envKeysLoading = ref(true)
const envKeys = ref([])

// Expanded env-key block, at most one at a time. Reset when the key it
// points at disappears from a fresh load (deleted, or renamed under a new
// name).
const expandedName = ref(null)
const editName = ref('')
const editUiDescription = ref('')
const editValue = ref('')
const editAiAccess = ref('none')
const editAiDefinition = ref('')

const AI_ACCESS_OPTIONS = [
  { value: 'none', label: 'None — scripts only' },
  { value: 'readonly', label: 'Read-only' },
  { value: 'readwrite', label: 'Read and write' }
]

function resetEditBuffers(entry) {
  editName.value = entry?.env_key.name ?? ''
  editUiDescription.value = entry?.env_key.ui_description ?? ''
  editValue.value = entry?.env_key.value ?? ''
  editAiAccess.value = entry?.env_key.ai_access ?? 'none'
  editAiDefinition.value = entry?.env_key.ai_definition ?? ''
}

// Function-ref instead of ref=: a v-for's ref string would collect one
// entry per row even though only the expanded row renders an editable
// name input.
let nameInputEl = null
function setNameInputRef(el) {
  nameInputEl = el
}
const blockRefs = {}
function setBlockRef(name, el) {
  if (el) blockRefs[name] = el
  else delete blockRefs[name]
}

function isRecentlyAdded(name) {
  return props.recentlyAddedKey === `env-key:${name}`
}

watch(() => props.recentlyAddedKey, async (key) => {
  if (!key?.startsWith('env-key:')) return
  const name = key.slice('env-key:'.length)
  const entry = envKeys.value.find((e) => e.env_key.name === name)
  if (!entry) return
  expandedName.value = name
  resetEditBuffers(entry)
  await nextTick()
  blockRefs[name]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  nameInputEl?.focus()
  nameInputEl?.select()
})

function selectEnvKey(entry) {
  const name = entry.env_key.name
  if (expandedName.value === name) {
    expandedName.value = null
  } else {
    expandedName.value = name
    resetEditBuffers(entry)
  }
  emit('jump-to-definition', { kind: 'env-key', envKeyName: name })
}

function commitField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', expandedName.value, field, currentValue)
}

async function loadEnvKeys() {
  envKeysLoading.value = true
  try {
    envKeys.value = (await getProjectEnvKeys(props.projectId)).env_keys
  } catch {} finally { envKeysLoading.value = false }
  if (expandedName.value && !envKeys.value.some((e) => e.env_key.name === expandedName.value)) {
    expandedName.value = null
  }
}

async function refresh() {
  await loadEnvKeys()
}

defineExpose({ loadEnvKeys, refresh })

onMounted(loadEnvKeys)
</script>

<template>
  <div class="inspector-signals-section">
    <p v-if="envKeysLoading" class="signals-status">Loading…</p>
    <p v-else-if="!envKeys.length" class="signals-status">No env keys declared.</p>
    <div v-else class="inspector-signal-list">
      <div
        v-for="entry in envKeys"
        :key="entry.env_key.name"
        :ref="(el) => setBlockRef(entry.env_key.name, el)"
        class="inspector-signal-block inspector-signal-block-clickable"
        :class="{ 'inspector-signal-block-flash': isRecentlyAdded(entry.env_key.name) }"
        title="Click to open"
        @click="selectEnvKey(entry)"
      >
        <Transition name="crossfade" mode="out-in">
          <div v-if="expandedName === entry.env_key.name" key="edit" class="inspector-signal-form">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-env">Env</span>
              <input
                :ref="setNameInputRef"
                v-model="editName"
                class="inspector-signal-label-input"
                placeholder="Name"
                @click.stop
                @blur="commitField('name', editName, entry.env_key.name)"
                @keydown.enter.prevent="handleEnterNext"
              />
              <CardMenu>
                <button type="button" class="card-menu-item-danger" @click="handleDeleteEnvKey(entry.env_key.name)">Delete</button>
              </CardMenu>
            </div>
            <label class="inspector-signal-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-signal-textarea"
              rows="2"
              @click.stop
              @blur="commitField('ui-description', editUiDescription, entry.env_key.ui_description ?? '')"
            ></textarea>
            <label class="inspector-signal-form-label" title="A Python expression, evaluated server-side">
              <span class="inspector-py-field-icon" title="Python expression">PY</span>
              Value
            </label>
            <TriggerEditor
              v-model="editValue"
              :exclude-namespaces="['actuator']"
              @click.stop
              @blur="commitField('value', editValue, entry.env_key.value ?? '')"
            />
            <label class="inspector-signal-form-label">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              AI access
            </label>
            <select
              v-model="editAiAccess"
              class="inspector-env-access-select"
              title="What the model may do with this key through an avance:env source — scripts always write any key"
              @click.stop
              @change="commitField('ai-access', editAiAccess, entry.env_key.ai_access ?? 'none')"
            >
              <option v-for="option in AI_ACCESS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <label class="inspector-signal-form-label">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              AI definition
            </label>
            <textarea
              v-model="editAiDefinition"
              v-autosize
              class="inspector-signal-textarea"
              rows="2"
              placeholder="What this variable means, written for the model — required once AI access isn't none."
              @click.stop
              @blur="commitField('ai-definition', editAiDefinition, entry.env_key.ai_definition ?? '')"
            ></textarea>
          </div>
          <div v-else key="readonly" class="inspector-signal-readonly">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-env">Env</span>
              <span class="inspector-signal-name">{{ entry.env_key.name }}</span>
              <span
                v-if="(entry.env_key.ai_access ?? 'none') !== 'none'"
                class="inspector-env-access"
                :class="'inspector-env-access-' + entry.env_key.ai_access"
                :title="entry.env_key.ai_access === 'readwrite' ? 'The model can read and update this key' : 'The model can read this key'"
              >{{ entry.env_key.ai_access }}</span>
              <CardMenu>
                <button type="button" class="card-menu-item-danger" @click="handleDeleteEnvKey(entry.env_key.name)">Delete</button>
              </CardMenu>
            </div>
            <span v-if="entry.env_key.ui_description" class="inspector-signal-ui_description">{{ entry.env_key.ui_description }}</span>
            <code v-if="entry.env_key.value" class="inspector-detail-code">{{ entry.env_key.value }}</code>
            <span v-if="entry.env_key.ai_definition" class="inspector-signal-ai_definition">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              {{ entry.env_key.ai_definition }}
            </span>
          </div>
        </Transition>
      </div>
    </div>
    <button class="inspector-signals-add-btn" @click="emit('add-env-key')">+ Add env key</button>
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-signals-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-signals-add-btn:hover { background: #eef2f9; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-list { display: flex; flex-direction: column; gap: 0.6rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
@keyframes inspector-signal-block-flash { from { background-color: #fff3b0; } to { background-color: #fafafa; } }
.inspector-signal-block-flash { animation: inspector-signal-block-flash 1.5s ease-out; }
.inspector-signal-block-clickable { cursor: pointer; }
.inspector-signal-block-clickable:hover { border-color: #c9d6e8; background: #f0f4fa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-env { background: #00838f; }
.inspector-signal-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-label-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; }
.inspector-signal-label-input:hover, .inspector-signal-label-input:focus { border-color: #ccc; background: white; }
.inspector-signal-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.15rem; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
/* Marks a field evaluated server-side as a Python expression (simpleeval). */
.inspector-py-field-icon { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 1.1rem; height: 0.85rem; border-radius: 3px; background: #4b8bbe; color: white; font-size: 0.55rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -0.02em; }
.inspector-signal-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.78rem; line-height: 1.54; padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-signal-ui_description { display: block; margin-top: 0.3rem; font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-signal-ai_definition { display: flex; align-items: flex-start; gap: 0.35rem; margin-top: 0.3rem; font-size: 0.78rem; color: #555; line-height: 1.4; }
/* Marks a field the AI itself reads, as opposed to a purely human-facing one like Description. */
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; margin-top: 0.15rem; }
.inspector-env-access-select { display: block; width: 100%; box-sizing: border-box; font: inherit; font-size: 0.78rem; padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; background: white; }
.inspector-env-access { flex-shrink: 0; font-size: 0.62rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.1rem 0.4rem; border-radius: 999px; color: white; background: #4a6fa5; }
.inspector-env-access-readwrite { background: #6d28d9; }
.inspector-detail-code { display: block; margin-top: 0.3rem; font-size: 0.78rem; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; color: #444; word-break: break-word; white-space: pre-wrap; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>
