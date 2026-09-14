<script setup>
import { nextTick, ref, watch } from 'vue'
import { vAutosize } from '../../../../components/skillkit/textareaAutosize.js'
import CardMenu from '../../../../components/skillkit/CardMenu.vue'
import TriggerEditor from '../../../../components/skillkit/TriggerEditor.vue'
import { handleEnterNext } from '../../../../components/skillkit/enterToNextField.js'

const props = defineProps({
  envKeys: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  recentlyAddedKey: { type: String, default: null }
})

const emit = defineEmits(['add-env-key', 'set-field', 'delete', 'jump-to-definition'])

function handleDeleteEnvKey(name) {
  emit('delete', name)
}

const expandedName = ref(null)
const editName = ref('')
const editUiDescription = ref('')
const editValue = ref('')
const editAiDefinition = ref('')

function resetEditBuffers(envKey) {
  editName.value = envKey?.name ?? ''
  editUiDescription.value = envKey?.ui_description ?? ''
  editValue.value = envKey?.value ?? ''
  editAiDefinition.value = envKey?.ai_definition ?? ''
}

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

watch(() => props.envKeys, (keys) => {
  if (expandedName.value && !keys.some((k) => k.name === expandedName.value)) {
    expandedName.value = null
  }
})

watch(() => props.recentlyAddedKey, async (key) => {
  if (!key?.startsWith('env-key:')) return
  const name = key.slice('env-key:'.length)
  const envKey = props.envKeys.find((k) => k.name === name)
  if (!envKey) return
  expandedName.value = name
  resetEditBuffers(envKey)
  await nextTick()
  blockRefs[name]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  nameInputEl?.focus()
  nameInputEl?.select()
})

function selectEnvKey(envKey) {
  if (expandedName.value === envKey.name) {
    expandedName.value = null
  } else {
    expandedName.value = envKey.name
    resetEditBuffers(envKey)
  }
  emit('jump-to-definition', { kind: 'env-key', envKeyName: envKey.name })
}

function commitField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', expandedName.value, field, currentValue)
}
</script>

<template>
  <div class="inspector-env-keys">
    <p v-if="loading" class="signals-status">Loading…</p>
    <p v-else-if="!envKeys.length" class="signals-status">No env keys declared.</p>
    <div v-else class="inspector-signal-list">
      <div
        v-for="envKey in envKeys"
        :key="envKey.name"
        :ref="(el) => setBlockRef(envKey.name, el)"
        class="inspector-signal-block inspector-signal-block-clickable"
        :class="{ 'inspector-signal-block-flash': isRecentlyAdded(envKey.name) }"
        title="Click to open"
        @click="selectEnvKey(envKey)"
      >
        <Transition name="crossfade" mode="out-in">
          <div v-if="expandedName === envKey.name" key="edit" class="inspector-signal-form">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-env">Env</span>
              <input
                :ref="setNameInputRef"
                v-model="editName"
                class="inspector-signal-label-input"
                placeholder="Name"
                @click.stop
                @blur="commitField('name', editName, envKey.name)"
                @keydown.enter.prevent="handleEnterNext"
              />
              <CardMenu>
                <button type="button" class="card-menu-item-danger" @click="handleDeleteEnvKey(envKey.name)">Delete</button>
              </CardMenu>
            </div>
            <label class="inspector-signal-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-signal-textarea"
              rows="2"
              @click.stop
              @blur="commitField('ui-description', editUiDescription, envKey.ui_description ?? '')"
            ></textarea>
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
              placeholder="What this variable means, written for the model. Required once some state lists it in its own input/output."
              @click.stop
              @blur="commitField('ai-definition', editAiDefinition, envKey.ai_definition ?? '')"
            ></textarea>
            <label class="inspector-signal-form-label" title="A Python expression, evaluated server-side">
              <span class="inspector-py-field-icon" title="Python expression">PY</span>
              Value
            </label>
            <TriggerEditor
              v-model="editValue"
              :exclude-namespaces="['task', 'chat']"
              @click.stop
              @blur="commitField('value', editValue, envKey.value ?? '')"
            />
          </div>
          <div v-else key="readonly" class="inspector-signal-readonly">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-env">Env</span>
              <span class="inspector-signal-name">{{ envKey.name }}</span>
              <CardMenu>
                <button type="button" class="card-menu-item-danger" @click="handleDeleteEnvKey(envKey.name)">Delete</button>
              </CardMenu>
            </div>
            <span v-if="envKey.ui_description" class="inspector-signal-ui_description">{{ envKey.ui_description }}</span>
            <span v-if="envKey.ai_definition" class="inspector-signal-ai_definition">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              {{ envKey.ai_definition }}
            </span>
            <code v-if="envKey.value" class="inspector-detail-code">{{ envKey.value }}</code>
          </div>
        </Transition>
      </div>
    </div>
    <button class="inspector-signals-add-btn" @click="emit('add-env-key')">+ Add env key</button>
  </div>
</template>

<style scoped>
.inspector-env-keys { display: flex; flex-direction: column; }
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
.inspector-py-field-icon { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 1.1rem; height: 0.85rem; border-radius: 3px; background: #4b8bbe; color: white; font-size: 0.55rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -0.02em; }
.inspector-signal-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.78rem; line-height: 1.54; padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-signal-ui_description { display: block; margin-top: 0.3rem; font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; margin-top: 0.15rem; }
.inspector-signal-ai_definition { display: flex; align-items: flex-start; gap: 0.35rem; margin-top: 0.3rem; font-size: 0.78rem; color: #555; line-height: 1.4; }
.inspector-detail-code { display: block; margin-top: 0.3rem; font-size: 0.78rem; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; color: #444; word-break: break-word; white-space: pre-wrap; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>
