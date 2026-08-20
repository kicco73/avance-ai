<script setup>
import { computed, nextTick, ref, watch, onMounted } from 'vue'
import { getProjectSignals } from '../../api.js'
import { hasSignalValue, useSignalChangeFlash } from './signalDisplay.js'
import { vAutosize } from './textareaAutosize.js'
import CardMenu from './CardMenu.vue'
import { handleEnterNext } from './enterToNextField.js'

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
  stateKey: { type: String, default: null },
  // See EditProjectView.vue's own docstring on this — 'signal:<name>'
  // while one of `signals` is the entry a "+ Add signal" click just
  // created, null otherwise.
  recentlyAddedKey: { type: String, default: null },
  // Whether the session being annotated was imported (see ChatSession.
  // source) — see InspectorGraph.vue's own imported prop docstring for
  // why: the "expected value set" overlay reads as a neutral "labelled"
  // green there instead of its usual magenta, matching ChatTimeline.vue/
  // InspectorGraph.vue's own imported-session styling.
  imported: { type: Boolean, default: false }
})

const emit = defineEmits(['jump-to-definition', 'select-attachment', 'update-expected-signals', 'set-field', 'add-signal', 'delete'])

// No confirmation dialog — an undo exists for exactly this (same
// reasoning as InspectorDetailCard.vue's own handleDelete).
function handleDeleteSignal(signalName) {
  emit('delete', signalName)
}

const signalsLoading = ref(true)
const signals = ref([])
const { recentlyChanged: recentlyChangedSignals, markChanged: markSignalsChanged } = useSignalChangeFlash()
const draggingExpectedValues = ref({})

// Which signal's own block is expanded into an editable form (see
// editableFiles below — same "we're inside an active edit session"
// signal already gating jump-to-definition/attachments) — at most one
// at a time, same convention as the Actions tab's own single-selected-
// row form. Local UI state only, never persisted, reset whenever the
// signal it points at disappears from a fresh load (a delete, or a
// rename that already updated `signals` under a new name).
const expandedSignalName = ref(null)
const editUiLabel = ref('')
const editUiDescription = ref('')
const editDefinition = ref('')

function resetEditBuffers(entry) {
  editUiLabel.value = entry?.signal.ui_label ?? ''
  editUiDescription.value = entry?.signal.ui_description ?? ''
  editDefinition.value = entry?.signal.definition ?? ''
}

// A plain (non-ref-array) element ref — see InspectorEnvTab.vue's own
// setEditInputRef for why: a v-for's own `ref=` would collect one entry
// per row regardless of how many actually render an editable label input
// at a time (only the expanded one does).
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

// "+ Add signal" (see EditProjectView.vue's own handleAddSignal) creates
// a signal with no trigger referencing it yet, so it's never "relevant" —
// left showOnlyRelevant on, it would silently vanish from the list the
// instant it's created (previously the user had to notice this and
// switch the toggle off by hand). Expanding it and focusing its label
// also fulfils "keep focus on the new signal" from the same request.
watch(() => props.recentlyAddedKey, async (key) => {
  if (!key?.startsWith('signal:')) return
  const name = key.slice('signal:'.length)
  const entry = signals.value.find((s) => s.signal.name === name)
  if (!entry) return
  showOnlyRelevant.value = false
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

// Within an active edit session (editableFiles set), a null stateKey
// means nothing at all is selected (see EditProjectView.vue's own
// selectedStateKey) — the filter has nothing to scope itself to, so it's
// bypassed entirely rather than falling back to "every state's triggers
// combined" (still what a null stateKey means for the live/Benchmark
// case below, where it's the deliberate fallback, not "nothing
// selected").
const displayedSignals = computed(() => {
  if (!showOnlyRelevant.value) return signals.value
  if (props.editableFiles && props.stateKey == null) return signals.value
  return signals.value.filter((s) => s.relevant)
})

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
  // A rename (or a delete) moves/removes the signal expandedSignalName
  // was pointing at — collapse rather than keep showing a stale form for
  // a name that's no longer in the freshly-loaded list.
  if (expandedSignalName.value && !signals.value.some((s) => s.signal.name === expandedSignalName.value)) {
    expandedSignalName.value = null
  }
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
      <div
        v-for="entry in displayedSignals"
        :key="entry.signal.name"
        :ref="(el) => setBlockRef(entry.signal.name, el)"
        class="inspector-signal-block"
        :class="{ 'inspector-signal-block-clickable': editableFiles, 'inspector-signal-block-flash': isRecentlyAdded(entry.signal.name) }"
        :title="editableFiles ? 'Click to open' : undefined"
        @click="editableFiles ? selectSignal(entry) : null"
      >
        <Transition name="crossfade" mode="out-in">
          <div v-if="editableFiles && expandedSignalName === entry.signal.name" key="edit" class="inspector-signal-form">
            <div class="inspector-signal-header">
              <span class="inspector-detail-badge inspector-detail-badge-signal">Signal</span>
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
              Definition
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
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
              <span class="inspector-detail-badge inspector-detail-badge-signal">Signal</span>
              <span class="inspector-signal-name">{{ entry.signal.ui_label || entry.signal.name }}</span>
              <CardMenu v-if="editableFiles">
                <button type="button" class="card-menu-item-danger" @click="handleDeleteSignal(entry.signal.name)">Delete</button>
              </CardMenu>
            </div>
            <span v-if="entry.signal.ui_description" class="inspector-signal-ui_description">{{ entry.signal.ui_description }}</span>
          </div>
        </Transition>
        <!-- The current-value bar is a live-conversation concept, same
             reasoning as EditProjectView.vue's own "no current state
             while editing" — never shown at all in an editable context
             (editableFiles), regardless of whether this block is
             expanded or collapsed. -->
        <template v-if="!editableFiles">
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
        </template>
      </div>
    </div>
    <button v-if="editableFiles" class="inspector-signals-add-btn" @click="emit('add-signal')">+ Add signal</button>
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-signals-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-signals-add-btn:hover { background: #eef2f9; }
.inspector-signals-relevant-toggle { display: flex; align-items: center; gap: 0.35rem; margin-bottom: 0.6rem; font-size: 0.78rem; color: #555; cursor: pointer; user-select: none; flex-shrink: 0; }
.inspector-signals-relevant-toggle input { cursor: pointer; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-list { display: flex; flex-direction: column; gap: 0.6rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
@keyframes inspector-signal-block-flash { from { background-color: #fff3b0; } to { background-color: #fafafa; } }
.inspector-signal-block-flash { animation: inspector-signal-block-flash 1.5s ease-out; }
.inspector-signal-block-clickable { cursor: pointer; }
.inspector-signal-block-clickable:hover { border-color: #c9d6e8; background: #f0f4fa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-signal { background: #6a4c93; }
.inspector-signal-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
/* Same hover/focus-reveal look as InspectorDetailCard.vue's own
   .inspector-detail-title-input, for a state/signal/action's own label
   to read as one consistent editing affordance across the Inspector. */
.inspector-signal-label-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-signal-label-input:hover, .inspector-signal-label-input:focus { border-color: #ccc; background: white; }
.inspector-signal-form-label { display: block; margin: 20px 0 0.15rem; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
/* Marks a field the AI itself reads (as opposed to a purely
   human-facing one like Description) — same purple used for its
   InspectorDetailCard.vue counterpart on Contextual prompt. */
.inspector-ai-field-icon { display: inline-flex; vertical-align: middle; margin-left: 0.3rem; color: #8b5cf6; }
.inspector-signal-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.78rem; line-height: 1.54; padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-signal-ui_description { font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-signal-bar-track { position: relative; margin-top: 0.4rem; height: 10px; border-radius: 999px; background: #eee; overflow: visible; }
.inspector-signal-bar-fill { height: 100%; background: #4a6fa5; border-radius: 999px; transition: width 0.3s ease; }
.inspector-signal-bar-na { width: 100%; background: repeating-linear-gradient(45deg, #ccc, #ccc 6px, #ddd 6px, #ddd 12px); }
.inspector-signal-expected-fill { position: absolute; inset: 0; height: 100%; border-radius: 999px; background: rgba(153, 153, 153, 0.3); pointer-events: none; transition: width 0.1s ease; }
.inspector-signal-expected-fill-set { background: rgba(173, 20, 87, 0.3); }
/* An imported session (see the imported prop's own docstring) has no
   avance-computed value to be "wrong" against — green "labelled" instead
   of the usual magenta "set", same green InspectorGraph.vue/
   ChatTimeline.vue use for their own imported-session styling. */
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
