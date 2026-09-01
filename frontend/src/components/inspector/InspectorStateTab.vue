<script setup>
// The Inspector's "Info" tab: the project's id/ui-label/ui-description on top,
// then either the shared Graph selection's detail card — a state OR an action,
// whichever is actually selected — plus "+ Add state"/"+ Add action" (Behavior
// node open), or the currently browsed file's read-only card (anything else —
// see isBehaviorContext). Owns its own project-metadata fetch, but not the
// selection itself.
import { computed, onMounted, ref, watch } from 'vue'
import { getProjectMetadata } from '../../api.js'
import InspectorDetailCard from './InspectorDetailCard.vue'
import InspectorProjectCard from './InspectorProjectCard.vue'
import InspectorFileCard from './InspectorFileCard.vue'
import SessionDetailCard from './SessionDetailCard.vue'
import ActionEnvEditor from './ActionEnvEditor.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
  // The action currently firing, for the detail card's own "Fired" badge —
  // only meaningful when selectedElement is an action.
  firedActionEdge: { type: Object, default: null },
  // Forwarded to InspectorDetailCard.vue when selectedElement is an
  // action — its target <select> options and Env editor's key suggestions.
  availableStates: { type: Array, default: () => [] },
  availableEnvKeys: { type: Array, default: () => [] },
  // See EditProjectView.vue's own docstring on this — 'state:<key>' while
  // `selectedElement` is the state a "+ Add state" click just created,
  // null otherwise.
  recentlyAddedKey: { type: String, default: null },
  // Design mode's currently open file — null outside 'edit' mode (see
  // EditProjectView.vue's own mode-gating for this prop). index.yml is
  // excluded here since it has no delete and its own dedicated tabs.
  currentFileName: { type: String, default: null },
  deletingFile: { type: String, default: null },
  // Auto mode's own selection (see EditProjectView.vue's autoSelected*
  // computeds) — a session read-only, in place of selectedElement's
  // state/action. { id, title, comment, type, ... }, same shape as
  // chatStore.js's sessions rows.
  selectedSession: { type: Object, default: null },
  sessionInputTokens: { type: Number, default: null },
  totalTokenBudgetPerSession: { type: Number, default: null },
  sessionStartElement: { type: Object, default: null },
  sessionEndElement: { type: Object, default: null },
  // Test mode only: no edit form, no delete, no "+ Add state" — this tab
  // is a plain read-only viewer for whatever's selected in the Test tree.
  readOnly: { type: Boolean, default: false },
  // Estimated input-token cost of selectedElement's own turn prompt (see
  // EditProjectView.vue's own stateTabTokens) — a separate prop rather
  // than folded into selectedElement.data, since that object round-trips
  // back out through the 'select' emit below and must stay exactly what
  // was passed in.
  stateTokens: { type: Number, default: null }
})

const emit = defineEmits([
  'select', 'select-attachment', 'jump-to-attachment', 'set-field', 'set-project-field', 'delete',
  'add-state', 'add-action', 'delete-file'
])

// True whenever there's no active file browsing to defer to (currentFileName
// is only ever non-null in edit mode — see EditProjectView.vue's own
// mode-gating) or index.yml itself is the open file — i.e. exactly when the
// Behavior node is selected. The state/action detail card and "+ Add state"
// only make sense then; a Theme file or a Behavior attachment gets the file
// card below instead.
const isBehaviorContext = computed(() => !props.currentFileName || props.currentFileName === 'index.yml')
const showFileCard = computed(() => !isBehaviorContext.value)

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

const projectMetadata = ref(null)

async function loadProjectMetadata() {
  try {
    projectMetadata.value = (await getProjectMetadata(props.projectName)).project
  } catch {
    // already surfaced via apiFetch
  }
}

// Inspector.vue's own registerTab dispatch — same "reload on demand, the shell
// never knows why" contract every other self-fetching tab (InspectorSignalsTab.vue/
// InspectorEnvKeysTab.vue) implements.
async function refresh() {
  await loadProjectMetadata()
}

defineExpose({ refresh })

onMounted(loadProjectMetadata)
</script>

<template>
  <div class="inspector-state-tab">
    <InspectorProjectCard
      v-if="!readOnly && !selectedElement"
      :project="projectMetadata"
      :editable="!readOnly"
      @set-field="(field, value) => emit('set-project-field', field, value)"
    />
    <InspectorFileCard
      v-if="showFileCard"
      :file-name="currentFileName"
      :deleting="deletingFile === currentFileName"
      @delete="emit('delete-file', currentFileName)"
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
      :selectable="!readOnly"
      :editable="!readOnly"
      :closable="false"
      :open="open"
      @update:open="open = $event"
      @select="emit('select', selectedElement)"
      @select-attachment="emit('select-attachment', $event)"
      @jump-to-attachment="emit('jump-to-attachment', $event)"
      @set-field="(field, value) => emit('set-field', field, value)"
      @delete="emit('delete', selectedElement)"
    />
    <ActionEnvEditor
      v-if="isBehaviorContext && !readOnly && open && selectedElement?.kind === 'action' && !selectedElement.data.isInitEdge"
      :env="selectedElement.data.env"
      :key-options="availableEnvKeys"
      @set-field="(field, value) => emit('set-field', field, value)"
    />
    <div v-if="isBehaviorContext && !readOnly && selectedElement?.kind !== 'action'" class="inspector-state-tab-add-row">
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
