<script setup>
// The read-only badge/fields/attachments card for whatever's selected in
// the Graph (a state or an action) — extracted out of InspectorGraphTab.vue
// so InspectorGraphTab.vue can compose this alongside InspectorGraph.vue
// (see that component's own docstring) instead of owning both concerns
// itself. Purely a function of `selectedElement` — every "is this the
// current/next/fired one" badge computed here from props, never from
// internal state, so a parent can drive this from Graph's own emitted
// selection without this component needing any cytoscape awareness at all.
import { computed, ref, watch } from 'vue'
import { vAutosize } from './textareaAutosize.js'
import CardMenu from './CardMenu.vue'
import TriggerEditor from './TriggerEditor.vue'
import { handleEnterNext } from './enterToNextField.js'

const props = defineProps({
  selectedElement: { type: Object, default: null }, // { kind: 'state' | 'action', data } | null
  editableFiles: { type: Array, default: null },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null },
  // Whether clicking the card's own body (not the × close button, not an
  // attachment button) emits 'select' — off by default, since the
  // original "States" tab usage (InspectorGraphTab.vue) has nothing
  // useful to do with a click on a card that's already the Graph's own
  // selection. The Inspector's "State"/"Actions" tabs turn this on: there
  // the same card can represent an element that isn't the literal shared
  // selection yet (e.g. "State" showing the state an already-selected
  // action merely belongs to) — clicking it promotes it to become that
  // selection, same as clicking it directly in the Graph would.
  selectable: { type: Boolean, default: false },
  // The × close button — on by default (the original floating-card
  // usage, and "State"). Off for a row inside "Actions"' own list: there
  // every action is always shown, nothing for × to "close" without also
  // meaning delete (a distinct, separate action of its own).
  closable: { type: Boolean, default: true },
  // Turns the read-only body into an editable form (see set-field below)
  // — only the Inspector's own "State"/"Actions" tabs (always inside an
  // active "Edit project" session) pass this; the original "States" tab
  // (InspectorGraphTab.vue, shown even outside any editing context, e.g.
  // the main chat window/LabelProjectView) leaves it at its default,
  // staying read-only exactly as before.
  editable: { type: Boolean, default: false },
  // Every real state's own {key, uiLabel} — the action form's own
  // target <select> options. Irrelevant (and unused) for a state card.
  availableStates: { type: Array, default: () => [] },
  // Whether this card's own form is showing, open/closed a v-model
  // (update:open) the *parent* owns rather than local state — the
  // Actions tab hosts several of these at once and needs exactly one
  // open at a time (an accordion), which only the parent (tracking one
  // shared "which one" value) can enforce; InspectorStateTab.vue, with
  // only ever one card, still owns its own single boolean the same way,
  // just trivially.
  open: { type: Boolean, default: false },
  // See EditProjectView.vue's own docstring on this — matched against
  // elementIdentity below to play a one-shot yellow-fade highlight when
  // this card is the state/action a "+ Add" click just created.
  recentlyAddedKey: { type: String, default: null },
  // A plain label ("START"/"END", ...) shown as its own badge, same slot
  // "Current" already occupies — for a caller that already knows *which*
  // state a card stands for by construction (see LabelProjectView.vue's
  // own Info tab: one card fixed to a session's start_state, another to
  // its end_state), rather than InspectorDetailCard re-deriving that from
  // a shared highlightedStateKey comparison the way isSelectedStateCurrent
  // does. State kind only; ignored for an action card.
  roleBadge: { type: String, default: null }
})

const emit = defineEmits(['select-attachment', 'jump-to-attachment', 'close', 'select', 'set-field', 'delete', 'update:open'])

const showEditForm = computed(() => props.editable && props.open)

// A click anywhere on the card's own background toggles open/closed and
// (when selectable) reselects — same convention as InspectorSignalsTab.
// vue's own signal blocks. Safe for the same reason those are: every
// actual form control inside (inputs, textareas, the target <select>,
// badges, the delete button, attachment buttons) already carries its own
// @click.stop, so a click that lands on one of those never reaches here
// at all — only a click on genuine background/whitespace does.
function handleCardClick() {
  if (props.editable) emit('update:open', !props.open)
  if (props.selectable) emit('select')
}

// Local editable buffers — separate from selectedElement's own props so
// typing doesn't fight a parent re-render, and so a blur can compare
// against the value it started from to skip a no-op PUT. Reset whenever
// the selection's own identity changes (switching from editing one
// state/action to another must never carry over the previous one's
// buffer, and must close the form back down rather than silently keep
// showing a stale one open) — never on every selectedElement prop
// change, since the same element's own reference changes after every
// refetch this component itself triggers (see EditProjectView.vue's
// refreshAfterProjectEdit).
const editUiLabel = ref('')
const editUiDescription = ref('')
const editContextualPrompt = ref('')
const editTrigger = ref('')
const editTarget = ref('')

const elementIdentity = computed(() => {
  if (!props.selectedElement) return null
  const d = props.selectedElement.data
  return props.selectedElement.kind === 'state' ? `state:${d.id}` : `action:${d.matchStateKey}/${d.actionName}`
})

const isRecentlyAdded = computed(() => props.recentlyAddedKey != null && props.recentlyAddedKey === elementIdentity.value)

function resetEditBuffers() {
  if (!props.selectedElement) return
  const d = props.selectedElement.data
  editUiLabel.value = d.uiLabel ?? ''
  editUiDescription.value = d.uiDescription ?? ''
  editContextualPrompt.value = d.contextualPrompt ?? ''
  editTrigger.value = d.trigger ?? ''
  editTarget.value = d.target ?? ''
}

watch(elementIdentity, resetEditBuffers, { immediate: true })
// Also on every fresh open — the parent now owns open/closed (see the
// `open` prop's own docstring), so this is what used to happen for free
// as a side effect of resetEditBuffers also closing the form on an
// identity change; opening back up should always start from whatever's
// actually current, not a possibly-stale buffer from before it closed.
watch(() => props.open, (isOpen) => { if (isOpen) resetEditBuffers() })

function commitTextField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', field, currentValue)
}

function commitUiLabel() {
  commitTextField('ui-label', editUiLabel.value, props.selectedElement?.data.uiLabel ?? '')
}

function commitUiDescription() {
  commitTextField('ui-description', editUiDescription.value, props.selectedElement?.data.uiDescription ?? '')
}

function commitContextualPrompt() {
  commitTextField('contextual-prompt', editContextualPrompt.value, props.selectedElement?.data.contextualPrompt ?? '')
}

function commitTrigger() {
  commitTextField('trigger', editTrigger.value, props.selectedElement?.data.trigger ?? '')
}

function commitTarget() {
  commitTextField('target', editTarget.value, props.selectedElement?.data.target ?? '')
}

// history-cutoff/chat: a plain instant toggle, not a typed field — no
// local buffer/blur dance needed, just commit straight off the prop's
// own current value (see the badge's own @click below).
function commitBoolField(field, value) {
  emit('set-field', field, value)
}

// A state that's currently the automaton's own start, or the init-action
// itself, isn't deletable at all — same rule the old inline "Delete"
// button enforced (see this file's own git history), just relocated onto
// this menu item.
const isDeleteDisabled = computed(() => {
  const d = props.selectedElement?.data
  if (!d) return false
  return props.selectedElement.kind === 'state' ? d.isStart : d.isInitEdge
})
const deleteDisabledReason = computed(() => {
  if (!isDeleteDisabled.value) return null
  return props.selectedElement.kind === 'state'
    ? "The initial state can't be deleted — point init-action at another state first."
    : "The init-action can't be deleted."
})

// No confirmation dialog — an undo exists for exactly this.
function handleDelete() {
  emit('delete')
}

function attachmentLabel(index) { return String.fromCharCode(97 + index) }

// An action's own source/target (see InspectorGraph.vue's own
// edgeToCyData) are real state *keys* — cytoscape itself requires that
// (they double as its own edge source/target ids) — never what a user
// should read as a label; resolved against availableStates for display,
// falling back to the raw key only for the synthetic pseudo-start id
// (never a real state, so never in availableStates at all).
function stateLabelFor(key) {
  return props.availableStates.find((s) => s.key === key)?.uiLabel ?? key
}

const isSelectedActionNext = computed(() => {
  if (props.selectedElement?.kind !== 'action' || !props.nextActionEdge) return false
  return (
    props.selectedElement.data.matchStateKey === props.nextActionEdge.stateKey &&
    props.selectedElement.data.actionName === props.nextActionEdge.actionName
  )
})

const isSelectedActionFired = computed(() => {
  if (props.selectedElement?.kind !== 'action' || !props.firedActionEdge) return false
  return (
    props.selectedElement.data.matchStateKey === props.firedActionEdge.stateKey &&
    props.selectedElement.data.actionName === props.firedActionEdge.actionName
  )
})

const isSelectedStateCurrent = computed(() => {
  return props.selectedElement?.kind === 'state' && props.selectedElement.data.id === props.highlightedStateKey
})

const hasSelectedElementBadges = computed(() => {
  if (!props.selectedElement) return false
  if (props.selectedElement.kind === 'state') {
    // History cutoff/No chat are always-shown clickable badges once the
    // form is actually open (see the template below) — so there's
    // always something to show then. Closed, this card is read-only,
    // same as the non-editable case entirely (see this component's own
    // docstring on `editable`/showEditForm) — same conditional set, no
    // clickable badges to change anything from here.
    if (showEditForm.value) return true
    const d = props.selectedElement.data
    return !!props.roleBadge || isSelectedStateCurrent.value || d.isStart || d.final || !d.chat || d.historyCutoff
  }
  // An open action's own badges are suppressed entirely (see the
  // template below) — only the "Action" kind-badge in the header stays,
  // which isn't part of this row at all. Closed, same read-only set as
  // the non-editable case.
  if (showEditForm.value) return false
  const d = props.selectedElement.data
  return isSelectedActionNext.value || isSelectedActionFired.value || !d.hasTrigger || d.isInitEdge
})

// Only ever reachable while showEditForm's own attachment list is
// actually showing (see its own template comment) — always editable by
// construction, so this only branches on state vs. action. A state's
// own editable form is the one place clicking an attachment should jump
// to where it's actually declared (index.yml's own `attachments:` list
// under that state) rather than open the attachment file itself — an
// action's own form keeps opening the file, unchanged.
function selectAttachment(fileName) {
  if (props.selectedElement?.kind === 'state') emit('jump-to-attachment', fileName)
  else emit('select-attachment', fileName)
}
</script>

<template>
  <div
    v-if="selectedElement"
    class="inspector-detail-card"
    :class="{ 'inspector-detail-card-selectable': selectable, 'inspector-detail-card-editable': editable, 'inspector-detail-card-open': showEditForm, 'inspector-detail-card-flash': isRecentlyAdded }"
    @click="handleCardClick"
  >
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span
          class="inspector-detail-badge"
          :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'"
        >{{ selectedElement.kind === 'state' ? 'State' : 'Action' }}</span>
        <input
          v-if="showEditForm"
          v-model="editUiLabel"
          class="inspector-detail-title-input"
          placeholder="Label"
          @click.stop
          @blur="commitUiLabel"
          @keydown.enter.prevent="handleEnterNext"
        />
        <span v-else class="inspector-detail-title">{{ selectedElement.data.uiLabel }}</span>
        <CardMenu v-if="editable">
          <button
            type="button"
            class="card-menu-item-danger"
            :disabled="isDeleteDisabled"
            :title="deleteDisabledReason"
            @click="handleDelete"
          >Delete</button>
        </CardMenu>
        <button v-if="closable" class="close-x-btn" title="Close" @click.stop="emit('close')">×</button>
      </div>
      <div v-if="hasSelectedElementBadges" class="inspector-detail-badges">
        <template v-if="selectedElement.kind === 'state'">
          <template v-if="!showEditForm">
            <span v-if="roleBadge" class="inspector-detail-badge inspector-detail-badge-current">{{ roleBadge }}</span>
            <span v-if="isSelectedStateCurrent" class="inspector-detail-badge inspector-detail-badge-current">Current</span>
            <span v-if="selectedElement.data.isStart" class="inspector-detail-badge inspector-detail-badge-start">Init</span>
            <span v-if="selectedElement.data.final" class="inspector-detail-badge inspector-detail-badge-final">Final</span>
          </template>
          <template v-if="showEditForm">
            <span
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="!selectedElement.data.chat ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
              title="Click to toggle"
              @click.stop="commitBoolField('chat', !selectedElement.data.chat)"
            >No chat</span>
            <span
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="selectedElement.data.historyCutoff ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
              title="Click to toggle"
              @click.stop="commitBoolField('history-cutoff', !selectedElement.data.historyCutoff)"
            >History cutoff</span>
          </template>
          <template v-else>
            <span v-if="!selectedElement.data.chat" class="inspector-detail-badge inspector-detail-badge-neutral">No chat</span>
            <span v-if="selectedElement.data.historyCutoff" class="inspector-detail-badge inspector-detail-badge-neutral">History cutoff</span>
          </template>
        </template>
        <template v-else-if="!showEditForm">
          <span v-if="selectedElement.data.isInitEdge" class="inspector-detail-badge inspector-detail-badge-start">Start</span>
          <span v-if="isSelectedActionNext" class="inspector-detail-badge inspector-detail-badge-next">Next</span>
          <span v-if="isSelectedActionFired" class="inspector-detail-badge inspector-detail-badge-fired">Fired</span>
          <span v-if="!selectedElement.data.hasTrigger" class="inspector-detail-badge inspector-detail-badge-manual">Manual</span>
        </template>
      </div>
    </div>
    <div class="inspector-detail-body">
      <template v-if="selectedElement.kind === 'state'">
        <Transition name="crossfade" mode="out-in">
          <div v-if="showEditForm" key="edit" class="inspector-detail-form">
            <label class="inspector-detail-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitUiDescription"
            ></textarea>
            <label class="inspector-detail-form-label">
              Contextual prompt
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
            </label>
            <textarea
              v-model="editContextualPrompt"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitContextualPrompt"
            ></textarea>
          </div>
          <div v-else key="readonly" class="inspector-detail-readonly">
            <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
          </div>
        </Transition>
      </template>
      <template v-else>
        <Transition name="crossfade" mode="out-in">
          <div v-if="showEditForm" key="edit" class="inspector-detail-form">
            <label class="inspector-detail-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitUiDescription"
            ></textarea>
            <template v-if="!selectedElement.data.isInitEdge">
              <label class="inspector-detail-form-label">Trigger</label>
              <TriggerEditor v-model="editTrigger" @click.stop @blur="commitTrigger" />
            </template>
            <p class="inspector-detail-field">
              <template v-if="!selectedElement.data.isInitEdge"><strong>{{ stateLabelFor(selectedElement.data.source) }}</strong> → </template>
              <select
                v-model="editTarget"
                class="inspector-detail-target-select"
                @click.stop
                @change="commitTarget"
              >
                <option v-for="state in availableStates" :key="state.key" :value="state.key">{{ state.uiLabel }}</option>
              </select>
            </p>
          </div>
          <div v-else key="readonly" class="inspector-detail-readonly">
            <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
            <p class="inspector-detail-field"><template v-if="!selectedElement.data.isInitEdge"><strong>{{ stateLabelFor(selectedElement.data.source) }}</strong> → </template><strong>{{ stateLabelFor(selectedElement.data.target) }}</strong></p>
            <p v-if="selectedElement.data.trigger" class="inspector-detail-field"><strong>Trigger:</strong><code class="inspector-detail-code">{{ selectedElement.data.trigger }}</code></p>
            <p v-if="selectedElement.data.onEnter" class="inspector-detail-field"><strong>On enter:</strong> {{ selectedElement.data.onEnter }}</p>
            <p v-if="selectedElement.data.actionPrompt" class="inspector-detail-field"><strong>Action prompt:</strong> {{ selectedElement.data.actionPrompt }}</p>
          </div>
        </Transition>
      </template>
      <!-- Attachments are an editing concern only — never shown in a
           read-only display, whether that's this card collapsed inside
           EditProjectView.vue's own editable State/Actions tabs, or the
           always-non-editable "States" tab (InspectorGraphTab.vue, shown
           during normal chat and throughout LabelProjectView.vue,
           `editable` false there). Only while the edit form itself is
           actually open (showEditForm: editable AND open) does the
           attachment list — and its own jump-to-definition/select
           affordance, see selectAttachment above — have anything useful
           to do. -->
      <div v-if="showEditForm && selectedElement.data.attachments?.length" class="inspector-attachments">
        <button
          v-for="(fileName, idx) in selectedElement.data.attachments"
          :key="fileName"
          class="inspector-attachment-btn"
          :class="{ 'inspector-attachment-btn-disabled': editableFiles && !editableFiles.includes(fileName) }"
          :disabled="editableFiles && !editableFiles.includes(fileName)"
          :title="!editableFiles || editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
          @click.stop="selectAttachment(fileName)"
        >{{ attachmentLabel(idx) }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
@keyframes inspector-detail-card-flash { from { background-color: #fff3b0; } to { background-color: #fafafa; } }
.inspector-detail-card-flash { animation: inspector-detail-card-flash 1.5s ease-out; }
.inspector-detail-card-selectable { cursor: pointer; }
.inspector-detail-card-selectable:hover { border-color: #c9d6e8; background: #f0f4fa; }
/* An editable card's own textarea can be dragged taller (resize:
   vertical, see .inspector-detail-textarea) — the 45%/overflow:hidden
   cap above exists for the read-only/list-row usages (Graph's floating
   card, Actions-list rows), where several cards share the panel at once.
   An editable card is always alone in its own already-scrollable tab
   (see .inspector-state-tab/.inspector-actions-tab's own overflow-y),
   so here the card should grow with its content instead of clipping or
   scrolling internally. */
.inspector-detail-card-editable { max-height: none; overflow: visible; }
.inspector-detail-card-editable .inspector-detail-body { overflow: visible; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-card-editable .inspector-detail-header { cursor: pointer; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-state { background: #4a6fa5; }
.inspector-detail-badge-action { background: #8a6d3b; }
.inspector-detail-badge-current { background: #f5a623; color: #3a2600; }
.inspector-detail-badge-start, .inspector-detail-badge-next { background: #2e7d32; }
.inspector-detail-badge-fired { background: #ad1457; }
.inspector-detail-badge-final { background: #c62828; }
.inspector-detail-badge-manual { background: #00695c; }
.inspector-detail-badge-neutral { background: #4a6fa5; }
.inspector-detail-badge-toggle { cursor: pointer; }
.inspector-detail-badge-toggle-off { background: #ccc; color: #555; }
.inspector-detail-badge-toggle-on { background: #4a6fa5; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-title-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-detail-title-input:hover, .inspector-detail-title-input:focus { border-color: #ccc; background: white; }
.inspector-detail-form-label { display: block; margin: 20px 0 0.2rem; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
/* Marks a field the AI itself reads (as opposed to a purely
   human-facing one like Description) — same purple used for its
   InspectorSignalsTab.vue counterpart on Definition. */
.inspector-ai-field-icon { display: inline-flex; vertical-align: middle; margin-left: 0.3rem; color: #8b5cf6; }
.inspector-detail-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.8rem; line-height: 1.54; padding: 0.4rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-detail-target-select { display: inline-block; width: auto; max-width: 100%; font: inherit; font-weight: 700; font-size: inherit; color: #333; padding: 0.05rem 0.2rem; border-radius: 4px; border: 1px solid transparent; background: transparent; cursor: pointer; }
.inspector-detail-target-select:hover, .inspector-detail-target-select:focus { border-color: #ccc; background: white; }
.close-x-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.close-x-btn:hover { background: #eee; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0 0 0.5rem; line-height: 1.4; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; }
.inspector-detail-code { font-size: 0.75rem; background: #eee; border-radius: 4px; padding: 0.1rem 0.4rem; }
.inspector-attachments { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.5rem; }
.inspector-attachment-btn { width: 1.5rem; height: 1.5rem; line-height: 1; border-radius: 4px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.72rem; font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.inspector-attachment-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.inspector-attachment-btn-disabled { border-color: #ccc; color: #aaa; cursor: not-allowed; }
.inspector-attachment-btn-disabled:hover { background: white; color: #aaa; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>
