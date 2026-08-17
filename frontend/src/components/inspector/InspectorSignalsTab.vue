<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import { getProjectSignals } from '../../api.js'
import { hasSignalValue, useSignalChangeFlash } from './signalDisplay.js'

const props = defineProps({
  projectName: { type: String, required: true },
  signalValues: { type: Object, default: () => ({}) },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  expectedValues: { type: Object, default: () => ({}) },
  // The state whose own outgoing actions `relevant` is scoped to (see
  // Inspector.vue's own relevantSignalsStateKey) — the Inspector's
  // currently selected/highlighted state, or the state a selected
  // action fires *from*. null means "every state's triggers combined"
  // (see getProjectSignals/backend's Automaton.
  // all_triggerable_signal_names).
  stateKey: { type: String, default: null }
})

const emit = defineEmits(['jump-to-definition', 'select-attachment', 'update-expected-signals'])

const signalsLoading = ref(true)
const signals = ref([])
const { recentlyChanged: recentlyChangedSignals, markChanged: markSignalsChanged } = useSignalChangeFlash()
const draggingExpectedValues = ref({})

// "Relevant" per-signal, scoped to props.stateKey (see Automaton.
// triggerable_signal_names/all_triggerable_signal_names on the backend)
// — computed server-side, authoritatively, from the same ast-based
// expression parsing the automaton itself uses to decide what a
// trigger/env: field can reference, not re-derived here: a client-side
// regex approximation over raw trigger text turned out to be exactly
// the kind of thing that's easy to get subtly wrong for no good reason
// when the server already knows the real answer. On by default
// (showOnlyRelevant) since a project's own declared signals often
// include ones the currently-selected state's own triggers don't read
// (still mid-authoring, intentionally advisory/display-only, or simply
// meaningful only to some *other* state).
const showOnlyRelevant = ref(true)

const displayedSignals = computed(() =>
  showOnlyRelevant.value ? signals.value.filter((s) => s.relevant) : signals.value
)

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

async function loadSignals() {
  signalsLoading.value = true
  try {
    signals.value = (await getProjectSignals(props.projectName, props.stateKey)).signals
  } catch {} finally { signalsLoading.value = false }
}

function selectAttachment(fileName) { emit('select-attachment', fileName) }

// Always reloads regardless of `active` — matches this tab's pre-slot-
// refactor behavior (Inspector.vue's old plain refresh() called
// loadSignals() unconditionally, since signalsLog feeds the chat
// timeline itself, visible whether or not this tab happens to be open).
async function refresh() {
  await loadSignals()
}

defineExpose({ loadSignals, refresh })

// A graph click (or the live state itself moving on) changes which
// state's own triggers `relevant` should reflect — refetch rather than
// filter a stale response, since the server (not this component) is
// the one deciding relevance now.
watch(() => props.stateKey, loadSignals)

onMounted(loadSignals)
</script>

<template>
  <div class="inspector-signals-section">
    <label v-if="!signalsLoading && signals.length" class="inspector-signals-relevant-toggle">
      <input type="checkbox" v-model="showOnlyRelevant" />
      Show only relevant signals
    </label>
    <p v-if="signalsLoading" class="signals-status">Loading…</p>
    <p v-else-if="!signals.length" class="signals-status">No signals defined.</p>
    <p v-else-if="!displayedSignals.length" class="signals-status">
      No relevant signals — none are referenced by an action's own trigger yet.
    </p>
    <div v-else class="inspector-signal-list">
      <div v-for="signal in displayedSignals" :key="signal.name" class="inspector-signal-block" :class="{ 'inspector-signal-block-clickable': editableFiles }" :title="editableFiles ? 'Jump to definition' : undefined" @click="editableFiles && emit('jump-to-definition', { kind: 'signal', signalName: signal.name })">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-signal">Signal</span>
          <span class="inspector-signal-name">{{ signal.ui_label || signal.name }}</span>
        </div>
        <span v-if="signal.ui_description" class="inspector-signal-ui_description">{{ signal.ui_description }}</span>
        <div v-if="editableFiles && signal.attachments?.length" class="inspector-attachments">
          <button v-for="(fileName, idx) in signal.attachments" :key="fileName" class="inspector-attachment-btn" :class="{ 'inspector-attachment-btn-disabled': !editableFiles.includes(fileName) }" :disabled="!editableFiles.includes(fileName)" :title="editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`" @click.stop="selectAttachment(fileName)">{{ attachmentLabel(idx) }}</button>
        </div>
        <div class="inspector-signal-bar-track">
          <div v-if="hasSignalValue(signalValues[signal.name])" class="inspector-signal-bar-fill" :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(signal.name) }" :style="{ width: signalValues[signal.name].value + '%' }"></div>
          <div v-else class="inspector-signal-bar-fill inspector-signal-bar-na" :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(signal.name) }"></div>
          <div v-if="annotatable" class="inspector-signal-expected-fill" :class="{ 'inspector-signal-expected-fill-set': isExpectedValueSet(signal.name) }" :style="{ width: displayedExpectedValue(signal.name) + '%' }"></div>
          <input v-if="annotatable" type="range" min="0" max="100" step="1" class="inspector-signal-slider" :class="{ 'inspector-signal-slider-set': isExpectedValueSet(signal.name) }" :value="displayedExpectedValue(signal.name)" :title="`Expected: ${expectedValues[signal.name] ?? '—'}`" @click.stop @input="onExpectedSignalInput(signal.name, $event.target.value)" @change="onExpectedSignalChange(signal.name, $event.target.value)" />
        </div>
        <div v-if="annotatable && isExpectedValueSet(signal.name)" class="inspector-signal-annotation-footer">
          <span class="inspector-signal-expected-label">Expected: {{ draggingExpectedValues[signal.name] ?? expectedValues[signal.name] }}</span>
          <button v-if="expectedValues[signal.name] != null" type="button" class="inspector-annotation-clear-btn" title="Remove annotation" @click.stop="onClearExpectedSignal(signal.name)">×</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-signals-relevant-toggle { display: flex; align-items: center; gap: 0.35rem; margin-bottom: 0.6rem; font-size: 0.78rem; color: #555; cursor: pointer; user-select: none; flex-shrink: 0; }
.inspector-signals-relevant-toggle input { cursor: pointer; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-list { display: flex; flex-direction: column; gap: 0.6rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-block-clickable { cursor: pointer; }
.inspector-signal-block-clickable:hover { border-color: #c9d6e8; background: #f0f4fa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-signal { background: #6a4c93; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-ui_description { font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-signal-bar-track { position: relative; margin-top: 0.4rem; height: 10px; border-radius: 999px; background: #eee; overflow: visible; }
.inspector-signal-bar-fill { height: 100%; background: #4a6fa5; border-radius: 999px; transition: width 0.3s ease; }
.inspector-signal-bar-na { width: 100%; background: repeating-linear-gradient(45deg, #ccc, #ccc 6px, #ddd 6px, #ddd 12px); }
.inspector-signal-expected-fill { position: absolute; inset: 0; height: 100%; border-radius: 999px; background: rgba(153, 153, 153, 0.3); pointer-events: none; transition: width 0.1s ease; }
.inspector-signal-expected-fill-set { background: rgba(173, 20, 87, 0.3); }
.inspector-signal-slider { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; -webkit-appearance: none; appearance: none; background: transparent; }
.inspector-signal-slider::-webkit-slider-runnable-track { background: transparent; height: 100%; }
.inspector-signal-slider::-moz-range-track { background: transparent; height: 100%; }
.inspector-signal-slider::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 14px; height: 14px; margin-top: -2px; border-radius: 50%; border: 2px solid white; background: #999; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4); cursor: grab; }
.inspector-signal-slider::-moz-range-thumb { width: 14px; height: 14px; border-radius: 50%; border: 2px solid white; background: #999; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4); cursor: grab; }
.inspector-signal-slider-set::-webkit-slider-thumb { background: #ad1457; }
.inspector-signal-slider-set::-moz-range-thumb { background: #ad1457; }
.inspector-signal-annotation-footer { display: flex; align-items: center; gap: 0.3rem; margin-top: 0.3rem; }
.inspector-signal-expected-label { font-size: 0.72rem; color: #ad1457; font-weight: 600; }
.inspector-annotation-clear-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.inspector-annotation-clear-btn:hover { background: #eee; }
@keyframes inspector-signal-bar-flash { 0% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0.7); filter: brightness(1.35); } 70% { box-shadow: 0 0 0 5px rgba(74, 111, 165, 0); } 100% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0); filter: brightness(1); } }
.inspector-signal-bar-changed { animation: inspector-signal-bar-flash 0.9s ease-out; }
</style>
