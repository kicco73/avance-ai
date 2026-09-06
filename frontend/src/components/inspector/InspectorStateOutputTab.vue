<script setup>
// Same card look as InspectorSignalsTab/InspectorEnvKeysTab, trimmed to
// what output actually has: no scope/relevance filtering, no ai-access,
// no attachments — those are signal/env-specific. output is state-scoped
// output_keys, see EditProjectView's own resyncSelectedGraphElement
// docstring for why a plain computed off stateData is enough (every
// project edit reassigns that object wholesale once the graph reload lands).
import { computed, nextTick, ref, watch } from 'vue'
import { vAutosize } from './textareaAutosize.js'
import CardMenu from './CardMenu.vue'
import { handleEnterNext } from './enterToNextField.js'

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, required: true },
  stateData: { type: Object, default: null },
  // 'output-key:<stateKey>/<name>' for the entry a "+ Add output field"
  // click just created, null otherwise.
  recentlyAddedKey: { type: String, default: null },
})

const emit = defineEmits(['add-output-key', 'set-field', 'delete'])

const outputKeys = computed(() => props.stateData?.output_keys || [])

// At most one block expanded at a time, same convention as the signal/env
// cards. Reset whenever that key disappears from a fresh load (deleted,
// or renamed under a new name).
const expandedName = ref(null)
const editUiLabel = ref('')
const editUiDescription = ref('')
const editAiDefinition = ref('')

function resetEditBuffers(key) {
  editUiLabel.value = key?.ui_label ?? ''
  editUiDescription.value = key?.ui_description ?? ''
  editAiDefinition.value = key?.ai_definition ?? ''
}

let labelInputEl = null
function setLabelInputRef(el) {
  labelInputEl = el
}
const blockRefs = {}
function setBlockRef(name, el) {
  if (el) blockRefs[name] = el
  else delete blockRefs[name]
}

function isRecentlyAdded(name) {
  return props.recentlyAddedKey === `output-key:${props.stateKey}/${name}`
}

watch(() => props.recentlyAddedKey, async (key) => {
  const prefix = `output-key:${props.stateKey}/`
  if (!key?.startsWith(prefix)) return
  const name = key.slice(prefix.length)
  const entry = outputKeys.value.find((k) => k.name === name)
  if (!entry) return
  expandedName.value = name
  resetEditBuffers(entry)
  await nextTick()
  blockRefs[name]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  labelInputEl?.focus()
  labelInputEl?.select()
})

function selectOutputKey(key) {
  if (expandedName.value === key.name) {
    expandedName.value = null
  } else {
    expandedName.value = key.name
    resetEditBuffers(key)
  }
}

function commitField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', expandedName.value, field, currentValue)
}

// No confirmation dialog — an undo exists for exactly this.
function deleteOutputKey(name) {
  emit('delete', name)
}
</script>

<template>
  <div class="inspector-signals-section">
    <p v-if="!outputKeys.length" class="signals-status">
      No output fields declared. Add one to let the LLM produce structured values this state can use in triggers and actions.
    </p>
    <div v-else class="inspector-signal-list">
      <div
        v-for="key in outputKeys"
        :key="key.name"
        :ref="(el) => setBlockRef(key.name, el)"
        class="inspector-signal-block inspector-signal-block-clickable"
        :class="{ 'inspector-signal-block-flash': isRecentlyAdded(key.name) }"
        title="Click to open"
        @click="selectOutputKey(key)"
      >
        <Transition name="crossfade" mode="out-in">
          <div v-if="expandedName === key.name" key="edit" class="inspector-signal-form">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-output">Output</span>
              <input
                :ref="setLabelInputRef"
                v-model="editUiLabel"
                class="inspector-signal-label-input"
                placeholder="Label"
                @click.stop
                @blur="commitField('ui_label', editUiLabel, key.ui_label ?? '')"
                @keydown.enter.prevent="handleEnterNext"
              />
              <CardMenu>
                <button type="button" class="card-menu-item-danger" @click="deleteOutputKey(key.name)">Delete</button>
              </CardMenu>
            </div>
            <label class="inspector-signal-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-signal-textarea"
              rows="2"
              @click.stop
              @blur="commitField('ui_description', editUiDescription, key.ui_description ?? '')"
            ></textarea>
            <label class="inspector-signal-form-label">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              AI definition (required)
            </label>
            <textarea
              v-model="editAiDefinition"
              v-autosize
              class="inspector-signal-textarea"
              rows="2"
              placeholder="What this output field means, written for the model."
              @click.stop
              @blur="commitField('ai_definition', editAiDefinition, key.ai_definition ?? '')"
            ></textarea>
          </div>
          <div v-else key="readonly" class="inspector-signal-readonly">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-output">Output</span>
              <span class="inspector-signal-name">{{ key.ui_label || key.name }}</span>
              <CardMenu>
                <button type="button" class="card-menu-item-danger" @click="deleteOutputKey(key.name)">Delete</button>
              </CardMenu>
            </div>
            <span v-if="key.ui_description" class="inspector-signal-ui_description">{{ key.ui_description }}</span>
            <span v-if="key.ai_definition" class="inspector-signal-ai_definition">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              {{ key.ai_definition }}
            </span>
          </div>
        </Transition>
      </div>
    </div>
    <button class="inspector-signals-add-btn" @click="emit('add-output-key')">+ Add output field</button>
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
.inspector-detail-badge-output { background: #004d40; }
.inspector-signal-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-label-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-signal-label-input:hover, .inspector-signal-label-input:focus { border-color: #ccc; background: white; }
.inspector-signal-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.15rem; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.inspector-signal-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.78rem; line-height: 1.54; padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-signal-ui_description { display: block; margin-top: 0.3rem; font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-signal-ai_definition { display: flex; align-items: flex-start; gap: 0.35rem; margin-top: 0.3rem; font-size: 0.78rem; color: #555; line-height: 1.4; }
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; margin-top: 0.15rem; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>
