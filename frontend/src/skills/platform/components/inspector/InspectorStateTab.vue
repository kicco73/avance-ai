<script setup>
// The Inspector's "Info" tab: the shared Graph selection's detail card —
// a state OR an action, whichever is actually selected — plus "+ Add
// state"/"+ Add action" (Behavior node open), or the currently browsed
// file's read-only card (anything else — see isBehaviorContext). Owns
// neither the selection nor any fetch of its own.
//
// The project card and the file card used to be here, with the platform
// routes that feed them. Both are the editor's alone, so both left for
// project/edit/EditorStateTab.vue, which mounts this tab underneath
// them.
//
// It also used to take a `readOnly` flag, for a second screen that wanted
// the reading half and got the whole editing surface with its writes
// switched off. That screen composes the same cards itself now — which is
// why they live in components/skillkit/ — and this file is the editing
// composition, with nothing to switch off.
import { computed, ref, watch } from 'vue'
import InspectorDetailCard from '../../../../components/skillkit/InspectorDetailCard.vue'
import InspectorSourceCard from './InspectorSourceCard.vue'
import SessionDetailCard from '../../../../components/skillkit/SessionDetailCard.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
  // The action currently firing, for the detail card's own "Fired" badge —
  // only meaningful when selectedElement is an action.
  firedActionEdge: { type: Object, default: null },
  // Forwarded to InspectorDetailCard.vue when selectedElement is an
  // action — its target <select> options.
  availableStates: { type: Array, default: () => [] },
  // Forwarded to InspectorDetailCard.vue's own ScriptEditDialog.vue — unlike
  // @set-field (fire-and-forget, every other field), that dialog's OK
  // button needs to know whether the write actually landed before
  // closing, which only a real awaited call (not a Vue emit) can answer.
  // EditProjectView.vue's own handleSetSelectedElementField.
  saveField: { type: Function, default: null },
  // See EditProjectView.vue's own docstring on this — 'state:<key>' while
  // `selectedElement` is the state a "+ Add state" click just created,
  // null otherwise.
  recentlyAddedKey: { type: String, default: null },
  // Design mode's currently open file — null outside 'edit' mode (see
  // EditProjectView.vue's own mode-gating for this prop). Read here only
  // to know that something else is being browsed, so the state/action
  // card stands down (see isBehaviorContext); the card that shows the
  // file is the editor's own, in project/edit/EditorStateTab.vue.
  currentFileName: { type: String, default: null },
  // The design tree's currently selected Source node (see FileExplorer.vue's
  // own "Sources" branch) — { name, ui_label, ui_description, url } | null.
  // Takes over the whole tab (see isSourceContext below), same as a
  // selected file does.
  selectedSource: { type: Object, default: null },
  deletingSource: { type: String, default: null },
  // True while the design tree's "Sources" branch header itself is
  // selected — no individual source chosen yet (see FileExplorer.vue's
  // own header click). Takes the tab over the same way isSourceContext
  // does for a real source, except there's no card to show for it: just
  // suppresses the project/state/file cards that would otherwise leak
  // through from whatever was selected before.
  sourcesRootSelected: { type: Boolean, default: false },
  // Auto mode's own selection (see EditProjectView.vue's autoSelected*
  // computeds) — a session read-only, in place of selectedElement's
  // state/action. { id, title, comment, type, ... }, same shape as
  // chatStore.js's sessions rows.
  selectedSession: { type: Object, default: null },
  sessionInputTokens: { type: Number, default: null },
  totalTokenBudgetPerSession: { type: Number, default: null },
  sessionStartElement: { type: Object, default: null },
  sessionEndElement: { type: Object, default: null },
  // Estimated input-token cost of selectedElement's own turn prompt (see
  // EditProjectView.vue's own stateTabTokens) — a separate prop rather
  // than folded into selectedElement.data, since that object round-trips
  // back out through the 'select' emit below and must stay exactly what
  // was passed in.
  stateTokens: { type: Number, default: null }
})

const emit = defineEmits([
  'select', 'select-attachment', 'jump-to-attachment', 'set-field', 'delete',
  'add-state', 'add-action', 'open-actions-order',
  'set-source-field', 'delete-source'
])

// A selected Source node takes the whole tab over — no project card,
// state/action card, file card, or "+ Add" row makes sense alongside it
// (see FileExplorer.vue's own "Sources" branch/EditProjectView.vue's
// selection wiring, which clears currentFileName's own graph selection
// the same way switching files already does).
const isSourceContext = computed(() => props.selectedSource != null)

// True whenever there's no active file browsing to defer to (currentFileName
// is only ever non-null in edit mode — see EditProjectView.vue's own
// mode-gating) or index.yml itself is the open file — i.e. exactly when the
// Behavior node is selected. The state/action detail card and "+ Add state"
// only make sense then; a Theme file or a Behavior attachment gets the file
// card below instead.
const isBehaviorContext = computed(() => (
  !isSourceContext.value && !props.sourcesRootSelected && (!props.currentFileName || props.currentFileName === 'index.yml')
))

// Same session, shown once with a combined badge rather than two
// identical cards — mirrors LabelProjectView.vue's own Info tab.
const sessionStartIsEnd = computed(() => (
  props.sessionStartElement != null && props.sessionStartElement.data.id === props.sessionEndElement?.data.id
))

// Same identity format InspectorDetailCard.vue's own elementIdentity and
// EditProjectView.vue's flashRecentlyAdded use — 'state:<key>' or
// 'action:<stateKey>/<actionName>'.
const elementIdentity = computed(() => {
  const el = props.selectedElement
  if (!el) return null
  return el.kind === 'state' ? `state:${el.data.id}` : `action:${el.data.matchStateKey}/${el.data.actionName}`
})

// This tab owns the detail card's open/closed state: closed whenever the
// selection moves to a different state/action, except when it moved there
// because "+ Add state"/"+ Add action" just created it — that one opens
// straight into its edit form.
const open = ref(false)
watch(elementIdentity, (identity) => {
  open.value = identity != null && props.recentlyAddedKey === identity
})

</script>

<template>
  <div class="inspector-state-tab">
    <InspectorSourceCard
      v-if="isSourceContext"
      :source="selectedSource"
      :deleting="deletingSource === selectedSource?.name"
      @set-field="(field, value) => emit('set-source-field', field, value)"
      @delete="emit('delete-source', selectedSource)"
    />

    <template v-if="selectedSession">
      <SessionDetailCard
        :session="selectedSession"
        :editable="false"
        :session-input-tokens="sessionInputTokens"
        :total-token-budget-per-session="totalTokenBudgetPerSession"
      />
      <InspectorDetailCard
        v-if="sessionStartIsEnd"
        :selected-element="sessionStartElement"
        :closable="false"
        role-badge="Start / End"
      />
      <template v-else>
        <InspectorDetailCard v-if="sessionStartElement" :selected-element="sessionStartElement" :closable="false" role-badge="Start" />
        <InspectorDetailCard v-if="sessionEndElement" :selected-element="sessionEndElement" :closable="false" role-badge="End" />
      </template>
    </template>

    <InspectorDetailCard
      v-else-if="selectedElement && isBehaviorContext"
      :selected-element="selectedElement"
      :state-tokens="stateTokens"
      :editable-files="editableFiles"
      :fired-action-edge="firedActionEdge"
      :highlighted-state-key="highlightedStateKey"
      :available-states="availableStates"
      :recently-added-key="recentlyAddedKey"
      :selectable="true"
      :editable="true"
      :save-field="saveField"
      :closable="false"
      :open="open"
      @update:open="open = $event"
      @select="emit('select', selectedElement)"
      @select-attachment="emit('select-attachment', $event)"
      @jump-to-attachment="emit('jump-to-attachment', $event)"
      @set-field="(field, value) => emit('set-field', field, value)"
      @delete="emit('delete', selectedElement)"
      @open-actions-order="emit('open-actions-order', selectedElement)"
    />
    <div v-if="isBehaviorContext && selectedElement?.kind !== 'action'" class="inspector-state-tab-add-row">
      <button v-if="!selectedElement" class="inspector-state-tab-add-btn" @click="emit('add-state')">+ Add state</button>
      <button v-else class="inspector-state-tab-add-btn" @click="emit('add-action')">+ Add action</button>
    </div>
  </div>
</template>

<style scoped>
.inspector-state-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-state-tab-add-row { flex-shrink: 0; display: flex; gap: 0.5rem; margin-top: 0.5rem; }
.inspector-state-tab-add-btn { flex: 1; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-state-tab-add-btn:hover { background: #eef2f9; }
</style>
