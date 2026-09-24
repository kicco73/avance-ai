<script setup>
import { nextTick, ref, watch } from 'vue'
import { hasSignalValue, useSignalChangeFlash } from './signalDisplay.js'
import { vAutosize } from '../../../../components/skillkit/textareaAutosize.js'
import CardMenu from '../../../../components/skillkit/CardMenu.vue'
import { handleEnterNext } from '../../../../components/skillkit/enterToNextField.js'

const props = defineProps({
  signals: { type: Array, required: true },
  signalValues: { type: Object, default: () => ({}) },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  expectedValues: { type: Object, default: () => ({}) },
  recentlyAddedKey: { type: String, default: null },
  imported: { type: Boolean, default: false },
  signalColors: { type: Object, default: null }
})

function badgeStyle(name) {
  if (props.signalColors == null) return null
  return { background: props.signalColors[name] ?? '#9e9e9e' }
}

const emit = defineEmits(['jump-to-definition', 'select-attachment', 'update-expected-signals', 'set-field', 'delete'])

function handleDeleteSignal(signalName) {
  emit('delete', signalName)
}

const { recentlyChanged: recentlyChangedSignals, markChanged: markSignalsChanged } = useSignalChangeFlash()
const draggingExpectedValues = ref({})

const expandedSignalName = ref(null)
const editUiLabel = ref('')
const editUiDescription = ref('')
const editDefinition = ref('')

function resetEditBuffers(entry) {
  editUiLabel.value = entry?.signal.ui_label ?? ''
  editUiDescription.value = entry?.signal.ui_description ?? ''
  editDefinition.value = entry?.signal.definition ?? ''
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
  return props.recentlyAddedKey === `signal:${name}`
}

watch(() => props.recentlyAddedKey, async (key) => {
  if (!key?.startsWith('signal:')) return
  const name = key.slice('signal:'.length)
  const entry = props.signals.find((s) => s.signal.name === name)
  if (!entry) return
  if (props.editableFiles) {
    expandedSignalName.value = name
    resetEditBuffers(entry)
  }
  await nextTick()
  blockRefs[name]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  labelInputEl?.focus()
  labelInputEl?.select()
})

function selectSignal(entry) {
  if (!props.editableFiles) return
  const name = entry.signal.name
  if (expandedSignalName.value === name) {
    expandedSignalName.value = null
  } else {
    expandedSignalName.value = name
    resetEditBuffers(entry)
  }
  emit('jump-to-definition', { kind: 'signal', signalName: name })
}

function commitSignalField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', expandedSignalName.value, field, currentValue)
}

function attachmentLabel(index) { return String.fromCharCode(97 + index) }

watch(() => props.signalValues, (nextValues, previousValues) => {
  const previous = Object.entries(previousValues || {}).map(([name, v]) => ({ name, ...v }))
  const next = Object.entries(nextValues || {}).map(([name, v]) => ({ name, ...v }))
  markSignalsChanged(previous, next)
})

function displayedExpectedValue(signalName) {
  if (draggingExpectedValues.value[signalName] != null) return draggingExpectedValues.value[signalName]
  if (props.expectedValues[signalName] != null) return props.expectedValues[signalName]
  return props.signalValues[signalName]?.value ?? 0
}

function isExpectedValueSet(signalName) {
  return draggingExpectedValues.value[signalName] != null || props.expectedValues[signalName] != null
}

function onExpectedSignalInput(signalName, rawValue) { draggingExpectedValues.value = { ...draggingExpectedValues.value, [signalName]: Number(rawValue) } }
function onExpectedSignalChange(signalName, rawValue) {
  emit('update-expected-signals', { ...props.expectedValues, [signalName]: Number(rawValue) })
  const next = { ...draggingExpectedValues.value }
  delete next[signalName]
  draggingExpectedValues.value = next
}

function onClearExpectedSignal(signalName) {
  const next = { ...props.expectedValues }
  delete next[signalName]
  emit('update-expected-signals', next)
}

watch(() => props.signals, (entries) => {
  if (expandedSignalName.value && !entries.some((s) => s.signal.name === expandedSignalName.value)) {
    expandedSignalName.value = null
  }
})

function selectAttachment(fileName) { emit('select-attachment', fileName) }

</script>

<template>
  <div class="inspector-signal-list">
    <div
      v-for="entry in signals"
      :key="entry.signal.name"
      :ref="(el) => setBlockRef(entry.signal.name, el)"
      class="inspector-signal-block"
      :class="{ 'inspector-signal-block-clickable': editableFiles, 'inspector-signal-block-flash': isRecentlyAdded(entry.signal.name), 'inspector-signal-block-untracked': !entry.relevant }"
      :title="editableFiles ? 'Click to open' : undefined"
      @click="editableFiles ? selectSignal(entry) : null"
    >
      <Transition name="crossfade" mode="out-in">
        <div v-if="editableFiles && expandedSignalName === entry.signal.name" key="edit" class="inspector-signal-form">
          <div class="inspector-signal-header">
            <span class="inspector-detail-badge inspector-detail-badge-signal" :style="badgeStyle(entry.signal.name)">Signal</span>
            <input
              :ref="setLabelInputRef"
              v-model="editUiLabel"
              class="inspector-signal-label-input"
              placeholder="Label"
              @click.stop
              @blur="commitSignalField('ui-label', editUiLabel, entry.signal.ui_label ?? '')"
              @keydown.enter.prevent="handleEnterNext"
            />
            <CardMenu>
              <button type="button" class="card-menu-item-danger" @click="handleDeleteSignal(entry.signal.name)">Delete</button>
            </CardMenu>
          </div>
          <label class="inspector-signal-form-label">Description</label>
          <textarea
            v-model="editUiDescription"
            v-autosize
            class="inspector-signal-textarea"
            rows="2"
            @click.stop
            @blur="commitSignalField('ui-description', editUiDescription, entry.signal.ui_description ?? '')"
          ></textarea>
          <label class="inspector-signal-form-label">
            <span class="inspector-ai-field-icon" title="Read by the AI">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
            </span>
            Definition
          </label>
          <textarea
            v-model="editDefinition"
            v-autosize
            class="inspector-signal-textarea"
            rows="2"
            @click.stop
            @blur="commitSignalField('definition', editDefinition, entry.signal.definition ?? '')"
          ></textarea>
          <div v-if="entry.attachments?.length" class="inspector-attachments">
            <button v-for="(fileName, idx) in entry.attachments" :key="fileName" class="inspector-attachment-btn" :class="{ 'inspector-attachment-btn-disabled': !editableFiles.includes(fileName) }" :disabled="!editableFiles.includes(fileName)" :title="editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`" @click.stop="selectAttachment(fileName)">{{ attachmentLabel(idx) }}</button>
          </div>
        </div>
        <div v-else key="readonly" class="inspector-signal-readonly">
          <div class="inspector-signal-header">
            <span class="inspector-detail-badge inspector-detail-badge-signal" :style="badgeStyle(entry.signal.name)">Signal</span>
            <span class="inspector-signal-name">{{ entry.signal.ui_label || entry.signal.name }}</span>
            <CardMenu v-if="editableFiles">
              <button type="button" class="card-menu-item-danger" @click="handleDeleteSignal(entry.signal.name)">Delete</button>
            </CardMenu>
          </div>
          <div class="inspector-signal-bar-track">
            <div v-if="hasSignalValue(signalValues[entry.signal.name])" class="inspector-signal-bar-fill" :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(entry.signal.name) }" :style="{ width: signalValues[entry.signal.name].value + '%' }"></div>
            <div v-else class="inspector-signal-bar-fill inspector-signal-bar-na" :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(entry.signal.name) }"></div>
            <div v-if="annotatable" class="inspector-signal-expected-fill" :class="{ 'inspector-signal-expected-fill-set': isExpectedValueSet(entry.signal.name) && !imported, 'inspector-signal-expected-fill-labelled': isExpectedValueSet(entry.signal.name) && imported }" :style="{ width: displayedExpectedValue(entry.signal.name) + '%' }"></div>
            <input v-if="annotatable" type="range" min="0" max="100" step="1" class="inspector-signal-slider" :class="{ 'inspector-signal-slider-set': isExpectedValueSet(entry.signal.name) && !imported, 'inspector-signal-slider-labelled': isExpectedValueSet(entry.signal.name) && imported }" :value="displayedExpectedValue(entry.signal.name)" :title="`Expected: ${expectedValues[entry.signal.name] ?? '—'}`" @click.stop @input="onExpectedSignalInput(entry.signal.name, $event.target.value)" @change="onExpectedSignalChange(entry.signal.name, $event.target.value)" />
          </div>
          <div v-if="annotatable && isExpectedValueSet(entry.signal.name)" class="inspector-signal-annotation-footer">
            <span class="inspector-signal-expected-label" :class="{ 'inspector-signal-expected-label-labelled': imported }">Expected: {{ draggingExpectedValues[entry.signal.name] ?? expectedValues[entry.signal.name] }}</span>
            <button v-if="expectedValues[entry.signal.name] != null" type="button" class="inspector-annotation-clear-btn" title="Remove annotation" @click.stop="onClearExpectedSignal(entry.signal.name)">×</button>
          </div>
          <span v-if="entry.signal.ui_description" class="inspector-signal-ui_description">{{ entry.signal.ui_description }}</span>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.inspector-signal-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); align-items: start; gap: 0.6rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
@keyframes inspector-signal-block-flash { from { background-color: #fff3b0; } to { background-color: #fafafa; } }
.inspector-signal-block-flash { animation: inspector-signal-block-flash 1.5s ease-out; }
.inspector-signal-block-clickable { cursor: pointer; }
.inspector-signal-block-untracked { opacity: 0.6; }
.inspector-signal-block-clickable:hover { border-color: #c9d6e8; background: #f0f4fa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-signal { background: #6a4c93; }
.inspector-signal-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-label-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-signal-label-input:hover, .inspector-signal-label-input:focus { border-color: #ccc; background: white; }
.inspector-signal-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.15rem; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; }
.inspector-signal-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.78rem; line-height: 1.54; padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-signal-ui_description { display: block; margin-top: 0.3rem; font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-signal-bar-track { position: relative; margin-top: 0.4rem; height: 10px; border-radius: 999px; background: #eee; overflow: visible; }
.inspector-signal-bar-fill { height: 100%; background: #4a6fa5; border-radius: 999px; transition: width 0.3s ease; }
.inspector-signal-bar-na { width: 100%; background: repeating-linear-gradient(45deg, #ccc, #ccc 6px, #ddd 6px, #ddd 12px); }
.inspector-signal-expected-fill { position: absolute; inset: 0; height: 100%; border-radius: 999px; background: rgba(153, 153, 153, 0.3); pointer-events: none; transition: width 0.1s ease; }
.inspector-signal-expected-fill-set { background: rgba(173, 20, 87, 0.3); }
.inspector-signal-expected-fill-labelled { background: rgba(46, 125, 50, 0.3); }
.inspector-signal-slider { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; -webkit-appearance: none; appearance: none; background: transparent; }
.inspector-signal-slider::-webkit-slider-runnable-track { background: transparent; height: 100%; }
.inspector-signal-slider::-moz-range-track { background: transparent; height: 100%; }
.inspector-signal-slider::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 14px; height: 14px; margin-top: -2px; border-radius: 50%; border: 2px solid white; background: #999; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4); cursor: grab; }
.inspector-signal-slider::-moz-range-thumb { width: 14px; height: 14px; border-radius: 50%; border: 2px solid white; background: #999; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4); cursor: grab; }
.inspector-signal-slider-set::-webkit-slider-thumb { background: #ad1457; }
.inspector-signal-slider-set::-moz-range-thumb { background: #ad1457; }
.inspector-signal-slider-labelled::-webkit-slider-thumb { background: #2e7d32; }
.inspector-signal-slider-labelled::-moz-range-thumb { background: #2e7d32; }
.inspector-signal-annotation-footer { display: flex; align-items: center; gap: 0.3rem; margin-top: 0.3rem; }
.inspector-signal-expected-label { font-size: 0.72rem; color: #ad1457; font-weight: 600; }
.inspector-signal-expected-label-labelled { color: #2e7d32; }
.inspector-annotation-clear-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.inspector-annotation-clear-btn:hover { background: #eee; }
@keyframes inspector-signal-bar-flash { 0% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0.7); filter: brightness(1.35); } 70% { box-shadow: 0 0 0 5px rgba(74, 111, 165, 0); } 100% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0); filter: brightness(1); } }
.inspector-signal-bar-changed { animation: inspector-signal-bar-flash 0.9s ease-out; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>
