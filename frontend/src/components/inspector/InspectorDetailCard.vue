<script setup>
// Card for whatever's selected in the Graph (a state or an action) —
// purely a function of `selectedElement` props, never internal state, so
// a parent can drive it without this component needing cytoscape awareness.
import { computed, ref, watch } from 'vue'
import { vAutosize } from './textareaAutosize.js'
import CardMenu from './CardMenu.vue'
import TriggerEditor from './TriggerEditor.vue'
import OnEnterDialog from './OnEnterDialog.vue'
import { handleEnterNext } from './enterToNextField.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'
import { customDialog } from '../../dialogStore.js'
import { useTokensBar } from '../../composables/useTokensBar.js'
import { identifierRegistry } from '../../identifierRegistry.js'

const props = defineProps({
  selectedElement: { type: Object, default: null }, // { kind: 'state' | 'action', data } | null
  editableFiles: { type: Array, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null },
  // Whether clicking the card body (not the × or an attachment button)
  // emits 'select' — on for a card that can represent an element other
  // than the current shared selection, so clicking it promotes it.
  selectable: { type: Boolean, default: false },
  // The × close button — off for a row inside an actions list, where
  // every action is always shown and × would be ambiguous with delete.
  closable: { type: Boolean, default: true },
  // Turns the read-only body into an editable form (see set-field below)
  // — only passed by callers inside an active edit session; elsewhere the
  // card stays read-only.
  editable: { type: Boolean, default: false },
  // Estimated input-token cost of a state's own turn prompt (see
  // EditProjectView.vue's own stateTabTokens) — null while unknown/
  // loading, or for an action card, which has none. Kept as its own prop
  // rather than folded into selectedElement.data, which round-trips back
  // out unmodified through the 'select' emit below.
  stateTokens: { type: Number, default: null },
  // Every state's {key, uiLabel} — options for the action form's target
  // <select>. Unused for a state card.
  availableStates: { type: Array, default: () => [] },
  // Whether the form is open — a v-model (update:open) the parent owns
  // rather than local state, since a list of these cards needs an
  // accordion (only one open at a time), which only a shared parent can enforce.
  open: { type: Boolean, default: false },
  // Matched against elementIdentity below to play a one-shot yellow-fade
  // highlight when this card is the state/action a "+ Add" click just created.
  recentlyAddedKey: { type: String, default: null },
  // A plain label ("START"/"END", ...) shown in the same badge slot as
  // "Current" — for a caller that already knows which state a card
  // stands for, rather than deriving it from highlightedStateKey. State only.
  roleBadge: { type: String, default: null },
  // The On enter dialog's own OK button needs to await a real save
  // result (see openOnEnterDialog below) — every other field here
  // commits fire-and-forget through the set-field emit instead, which
  // can't report back whether its write actually landed.
  saveField: { type: Function, default: null }
})

const emit = defineEmits(['select-attachment', 'jump-to-attachment', 'close', 'select', 'set-field', 'delete', 'update:open', 'open-actions-order'])

const showEditForm = computed(() => props.editable && props.open)

// stateTokens' own bar — see useTokensBar.js. The exact number stays
// available on hover via the floating tooltip below.
const TOKENS_BAR_MAX = 1000
const { width: tokensBarWidth, level: tokensBarLevel } = useTokensBar(computed(() => props.stateTokens), TOKENS_BAR_MAX)
const {
  visible: tokensTooltipVisible, style: tokensTooltipStyle, show: showTokensTooltip, hide: hideTokensTooltip
} = useFloatingTooltip()

// A click anywhere on the card background toggles open/closed and (when
// selectable) reselects. Safe because every actual form control inside
// already carries its own @click.stop, so only whitespace reaches here.
function handleCardClick() {
  if (props.editable) emit('update:open', !props.open)
  if (props.selectable) emit('select')
}

// Local editable buffers, separate from selectedElement's props so
// typing doesn't fight a parent re-render. Reset on identity change only
// — the prop's own reference changes after every refetch this triggers.
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
// Also reset on every fresh open, so reopening always starts from
// whatever's actually current rather than a stale buffer left over from
// before it closed.
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

// The "On enter" badge (action cards only) opens the same TriggerEditor
// the inline form used to carry, just full-size in a dialog (a 20-line
// actuator script needs real room, unlike the other one-line fields
// above). onCommit goes through saveField (a real awaited call), not
// commitTextField/set-field (fire-and-forget) — OnEnterDialog.vue's own
// OK button needs the actual save result to know whether to close.
// Wire key is "on-enter" (kebab-case, not "onEnter") — written straight
// into the YAML under that literal key on the backend, same convention
// every other field here follows ('ui-label', 'history-cutoff', ...).
function openOnEnterDialog() {
  customDialog({
    component: OnEnterDialog,
    wide: true,
    props: {
      initialValue: props.selectedElement?.data.onEnter ?? '',
      excludeNamespaces: ['session'],
      onCommit: (value) => props.saveField('on-enter', value)
    }
  })
}

// history-cutoff/chat: a plain instant toggle, not a typed field — no
// local buffer/blur dance needed.
function commitBoolField(field, value) {
  emit('set-field', field, value)
}

// ai-may-read-sources / ai-must-read-sources — every declared source
// name is a 3-state read toggle (off -> may -> must -> off), same idiom
// as the boolean badges above; identifierRegistry.value.source is already
// the live-refreshed source of truth ModelMenu/TriggerEditor's own
// autocomplete uses, so no separate fetch is needed here. A source whose
// driver implements `update` (registry key "source.<name>" lists it —
// today only avance:env) additionally gets a separate write toggle
// (ai-may-write-sources): reading and writing are independent grants.
const availableSourceNames = computed(() => Object.keys(identifierRegistry.value.source ?? {}))
function sourceSupportsWrite(name) {
  return 'update' in (identifierRegistry.value[`source.${name}`] ?? {})
}
function toolState(name) {
  if ((props.selectedElement?.data.aiMustReadSources ?? []).includes(name)) return 'must'
  if ((props.selectedElement?.data.aiMayReadSources ?? []).includes(name)) return 'may'
  return 'off'
}
function cycleTool(name) {
  const may = props.selectedElement?.data.aiMayReadSources ?? []
  const must = props.selectedElement?.data.aiMustReadSources ?? []
  const current = toolState(name)
  if (current === 'off') {
    emit('set-field', 'ai-may-read-sources', [...may, name])
  } else if (current === 'may') {
    emit('set-field', 'ai-may-read-sources', may.filter((t) => t !== name))
    emit('set-field', 'ai-must-read-sources', [...must, name])
  } else {
    emit('set-field', 'ai-must-read-sources', must.filter((t) => t !== name))
  }
}
function writeState(name) {
  return (props.selectedElement?.data.aiMayWriteSources ?? []).includes(name)
}
function toggleWrite(name) {
  const write = props.selectedElement?.data.aiMayWriteSources ?? []
  emit('set-field', 'ai-may-write-sources', writeState(name) ? write.filter((t) => t !== name) : [...write, name])
}

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

const envEntries = computed(() => Object.entries(props.selectedElement?.data.env ?? {}))

function attachmentLabel(index) { return String.fromCharCode(97 + index) }

// source/target are real state keys, not labels — cytoscape requires
// them as-is for its own edge ids. Resolved against availableStates for
// display; falls back to the raw key only for the synthetic pseudo-start id.
function stateLabelFor(key) {
  return props.availableStates.find((s) => s.key === key)?.uiLabel ?? key
}

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
    // form is open, so there's always something to show then. Closed,
    // this card falls back to the same read-only badge set as non-editable.
    if (showEditForm.value) return true
    const d = props.selectedElement.data
    return !!props.roleBadge || isSelectedStateCurrent.value || d.isStart || d.final || !d.chat || d.historyCutoff ||
      (d.reactionsEnabled && d.hasReactions) || (d.aiMayQuerySources?.length > 0) || (d.aiMustQuerySources?.length > 0)
  }
  // "On enter" is an always-shown clickable badge once the form is open
  // — same reasoning, and same layout position (first in this row), as
  // No chat/History cutoff above. Closed, same read-only set as
  // non-editable, plus "On enter" itself whenever the action has one.
  if (showEditForm.value) return true
  const d = props.selectedElement.data
  return isSelectedActionFired.value || !d.hasTrigger || d.isInitEdge || !!d.onEnter
})

// Only reachable while the edit form's attachment list is showing. A
// state's form jumps to where the attachment is declared in index.yml;
// an action's form opens the attachment file directly.
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
        <CardMenu v-if="editable" v-slot="{ close }">
          <button
            v-if="selectedElement.kind === 'state'"
            type="button"
            @click="close(); emit('open-actions-order')"
          >Actions order</button>
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
            <span
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="[
                selectedElement.data.reactionsEnabled ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off',
                { 'inspector-detail-badge-toggle-locked': !selectedElement.data.hasReactions }
              ]"
              :title="selectedElement.data.hasReactions ? 'Click to toggle' : 'This project declares no reactions — add one in the Reactions tab first.'"
              @click.stop="selectedElement.data.hasReactions && commitBoolField('reactions-enabled', !selectedElement.data.reactionsEnabled)"
            >Reactions</span>
            <span
              v-for="name in availableSourceNames" :key="'tool-' + name"
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="{
                'inspector-detail-badge-toggle-off': toolState(name) === 'off',
                'inspector-detail-badge-toggle-on': toolState(name) === 'may',
                'inspector-detail-badge-toggle-required': toolState(name) === 'must'
              }"
              :title="
                toolState(name) === 'must'
                  ? `Forced: the model must read source.${name} once per entry into this state — click to turn off`
                  : toolState(name) === 'may'
                    ? `The model may read source.${name} (select) — click to force it (ai-must-read-sources)`
                    : `Click to let the model read source.${name} (select) while replying in this state`
              "
              @click.stop="cycleTool(name)"
            >{{ name }}</span>
            <span
              v-for="name in availableSourceNames.filter(sourceSupportsWrite)" :key="'tool-write-' + name"
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="writeState(name) ? 'inspector-detail-badge-toggle-write' : 'inspector-detail-badge-toggle-off'"
              :title="
                writeState(name)
                  ? `The model may write source.${name} (update) in this state — click to turn off`
                  : `Click to let the model write source.${name} (update) while replying in this state`
              "
              @click.stop="toggleWrite(name)"
            >{{ name }} ✎</span>
          </template>
          <template v-else>
            <span v-if="!selectedElement.data.chat" class="inspector-detail-badge inspector-detail-badge-neutral">No chat</span>
            <span v-if="selectedElement.data.historyCutoff" class="inspector-detail-badge inspector-detail-badge-neutral">History cutoff</span>
            <span v-if="selectedElement.data.reactionsEnabled && selectedElement.data.hasReactions" class="inspector-detail-badge inspector-detail-badge-neutral">Reactions</span>
            <span
              v-for="name in (selectedElement.data.aiMayReadSources || [])" :key="'tool-ro-may-' + name"
              class="inspector-detail-badge inspector-detail-badge-neutral"
              :title="`Readable by the model as source.${name} (its own choice)`"
            >{{ name }}</span>
            <span
              v-for="name in (selectedElement.data.aiMustReadSources || [])" :key="'tool-ro-must-' + name"
              class="inspector-detail-badge inspector-detail-badge-toggle-required"
              :title="`Forced: the model must read source.${name} once per entry into this state`"
            >{{ name }}</span>
            <span
              v-for="name in (selectedElement.data.aiMayWriteSources || [])" :key="'tool-ro-write-' + name"
              class="inspector-detail-badge inspector-detail-badge-toggle-write"
              :title="`Writable by the model as source.${name} (update)`"
            >{{ name }} ✎</span>
          </template>
        </template>
        <template v-else>
          <button
            v-if="showEditForm || selectedElement.data.onEnter"
            type="button"
            class="inspector-detail-badge inspector-detail-badge-toggle inspector-detail-badge-onenter-btn"
            :class="selectedElement.data.onEnter ? ['inspector-detail-badge-toggle-on', 'inspector-detail-badge-onenter'] : 'inspector-detail-badge-toggle-off'"
            :disabled="!editable"
            title="On enter"
            @click.stop="openOnEnterDialog()"
          >On enter</button>
          <template v-if="!showEditForm">
            <span v-if="selectedElement.data.isInitEdge" class="inspector-detail-badge inspector-detail-badge-start">Start</span>
            <span v-if="isSelectedActionFired" class="inspector-detail-badge inspector-detail-badge-fired">Fired</span>
            <span v-if="!selectedElement.data.hasTrigger" class="inspector-detail-badge inspector-detail-badge-manual">Manual</span>
          </template>
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
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              Contextual prompt
            </label>
            <textarea
              v-model="editContextualPrompt"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitContextualPrompt"
            ></textarea>
            <div v-if="stateTokens != null" class="inspector-detail-tokens">
              <span class="inspector-ai-field-icon" title="Estimated by the AI provider">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              <span class="inspector-detail-tokens-label">Tokens</span>
              <div
                class="inspector-detail-tokens-bar-track"
                @mouseenter="showTokensTooltip($event.currentTarget)"
                @mouseleave="hideTokensTooltip"
              >
                <div
                  class="inspector-detail-tokens-bar-fill"
                  :class="`inspector-detail-tokens-bar-fill-${tokensBarLevel}`"
                  :style="{ width: tokensBarWidth }"
                ></div>
              </div>
            </div>
          </div>
          <div v-else key="readonly" class="inspector-detail-readonly">
            <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
            <div v-if="stateTokens != null" class="inspector-detail-tokens">
              <span class="inspector-ai-field-icon" title="Estimated by the AI provider">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              <span class="inspector-detail-tokens-label">Tokens</span>
              <div
                class="inspector-detail-tokens-bar-track"
                @mouseenter="showTokensTooltip($event.currentTarget)"
                @mouseleave="hideTokensTooltip"
              >
                <div
                  class="inspector-detail-tokens-bar-fill"
                  :class="`inspector-detail-tokens-bar-fill-${tokensBarLevel}`"
                  :style="{ width: tokensBarWidth }"
                ></div>
              </div>
            </div>
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
              <label class="inspector-detail-form-label" title="A Python expression, evaluated server-side">
                <span class="inspector-py-field-icon" title="Python expression">PY</span>
                Trigger
              </label>
              <TriggerEditor v-model="editTrigger" :exclude-namespaces="['actuator']" @click.stop @blur="commitTrigger" />
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
            <p v-if="envEntries.length" class="inspector-detail-field">
              <strong>Env:</strong>
              <code v-for="[key, value] in envEntries" :key="key" class="inspector-detail-code">{{ key }} = {{ value }}</code>
            </p>
            <p v-if="selectedElement.data.trigger" class="inspector-detail-field"><strong>Trigger:</strong><code class="inspector-detail-code">{{ selectedElement.data.trigger }}</code></p>
          </div>
        </Transition>
      </template>
      <!-- Attachments are an editing concern only — never shown in a
           read-only display, and only while the edit form is actually
           open (showEditForm) does the attachment list do anything useful. -->
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
    <Teleport to="body">
      <span v-if="tokensTooltipVisible" class="inspector-detail-tokens-tooltip-floating" :style="tokensTooltipStyle">{{ stateTokens }} tokens</span>
    </Teleport>
  </div>
</template>

<style scoped>
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
@keyframes inspector-detail-card-flash { from { background-color: #fff3b0; } to { background-color: #fafafa; } }
.inspector-detail-card-flash { animation: inspector-detail-card-flash 1.5s ease-out; }
.inspector-detail-card-selectable { cursor: pointer; }
.inspector-detail-card-selectable:hover { border-color: #c9d6e8; background: #f0f4fa; }
/* The 45%/overflow:hidden cap above is for read-only/list-row usages,
   where several cards share the panel. An editable card is always alone
   in its own scrollable tab, so it should grow with its content instead. */
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
.inspector-detail-badge-start { background: #2e7d32; }
.inspector-detail-badge-fired { background: #ad1457; }
.inspector-detail-badge-final { background: #c62828; }
.inspector-detail-badge-manual { background: #00695c; }
.inspector-detail-badge-neutral { background: #4a6fa5; }
.inspector-detail-badge-toggle { cursor: pointer; }
.inspector-detail-badge-toggle-off { background: #ccc; color: #555; }
.inspector-detail-badge-toggle-on { background: #4a6fa5; }
/* ai-must-read-sources — a forced read, visually distinct from the
   plain "on" (ai-may-read-sources) toggle color above. */
.inspector-detail-badge-toggle-required { background: #c2410c; }
/* ai-may-write-sources — a write grant, its own color again: reading and
   writing the same source are independent toggles. */
.inspector-detail-badge-toggle-write { background: #6d28d9; }
.inspector-detail-badge-toggle-locked { cursor: not-allowed; opacity: 0.5; }
/* Same look and feel as the toggle family above (border-radius/padding/
   cursor/grey-when-off all come from -badge/-toggle/-toggle-off) — only
   its own active-state color differs, so this is the one declaration
   left to override, and only together with -toggle-on (compound
   selector, so it wins regardless of source order). */
.inspector-detail-badge-onenter-btn { appearance: none; border: none; margin: 0; font-family: inherit; cursor: pointer; }
.inspector-detail-badge-onenter-btn:disabled { cursor: not-allowed; opacity: 0.6; }
.inspector-detail-badge-onenter.inspector-detail-badge-toggle-on { background: #4b8bbe; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-title-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-detail-title-input:hover, .inspector-detail-title-input:focus { border-color: #ccc; background: white; }
.inspector-detail-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.2rem; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
/* Marks a field the AI itself reads, as opposed to a purely
   human-facing one like Description. */
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; }
/* Marks a field evaluated server-side as a Python expression. */
.inspector-py-field-icon { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 1.1rem; height: 0.85rem; border-radius: 3px; background: #4b8bbe; color: white; font-size: 0.55rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -0.02em; }
.inspector-detail-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.8rem; line-height: 1.54; padding: 0.4rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-detail-target-select { display: inline-block; width: auto; max-width: 100%; font: inherit; font-weight: 700; font-size: inherit; color: #333; padding: 0.05rem 0.2rem; border-radius: 4px; border: 1px solid transparent; background: transparent; cursor: pointer; }
.inspector-detail-target-select:hover, .inspector-detail-target-select:focus { border-color: #ccc; background: white; }
.close-x-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.close-x-btn:hover { background: #eee; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0 0 0.5rem; line-height: 1.4; }
.inspector-detail-tokens { display: flex; align-items: center; gap: 0.4rem; margin: 0.4rem 0 0; }
.inspector-detail-tokens-label { flex-shrink: 0; font-size: 0.72rem; color: #888; }
.inspector-detail-tokens-bar-track { position: relative; flex: 1; min-width: 40px; height: 8px; border-radius: 999px; background: #eee; overflow: hidden; cursor: default; }
.inspector-detail-tokens-bar-fill { height: 100%; border-radius: 999px; transition: width 0.3s ease; }
.inspector-detail-tokens-bar-fill-green { background: #2e7d32; }
.inspector-detail-tokens-bar-fill-orange { background: #f5a623; }
.inspector-detail-tokens-bar-fill-red { background: #c62828; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; }
.inspector-detail-code { display: inline-block; margin: 0.15rem 0.3rem 0 0; font-size: 0.75rem; background: #eee; border-radius: 4px; padding: 0.1rem 0.4rem; }
.inspector-attachments { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.5rem; }
.inspector-attachment-btn { width: 1.5rem; height: 1.5rem; line-height: 1; border-radius: 4px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.72rem; font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.inspector-attachment-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.inspector-attachment-btn-disabled { border-color: #ccc; color: #aaa; cursor: not-allowed; }
.inspector-attachment-btn-disabled:hover { background: white; color: #aaa; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>

<style>
/* Unscoped: teleported to <body> (see the tokens bar's tooltip above),
   outside this component's normal DOM subtree — same reasoning as
   ModelMenu.vue's own unscoped Teleport styles. */
.inspector-detail-tokens-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}
</style>
