<script setup>
// The Inspector's "Info" tab: the project's id/ui-label/ui-description on top,
// then either the shared Graph selection's detail card + "+ Add state" (Behavior
// node open) or the currently browsed file's read-only card (anything else — see
// isBehaviorContext). Owns its own project-metadata fetch, but not the state
// selection itself.
import { computed, onMounted, ref, watch } from 'vue'
import { getProjectMetadata } from '../../api.js'
import InspectorDetailCard from './InspectorDetailCard.vue'
import InspectorProjectCard from './InspectorProjectCard.vue'
import InspectorFileCard from './InspectorFileCard.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
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
  'select', 'select-attachment', 'jump-to-attachment', 'set-field', 'set-project-field', 'delete', 'add-state', 'delete-file'
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

// This tab owns the detail card's open/closed state: closed whenever the
// selection moves to a different state, except when it moved there because
// "+ Add state" just created it — that one opens straight into its edit form.
const open = ref(false)
watch(() => props.selectedElement?.data.id, (id) => {
  open.value = id != null && props.recentlyAddedKey === `state:${id}`
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
      v-if="!readOnly"
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
      <div class="inspector-signal-block">
        <div class="inspector-signal-readonly">
          <div class="inspector-signal-header">
            <span class="inspector-detail-badge inspector-detail-badge-session">Session</span>
            <span class="inspector-signal-name">{{ selectedSession.title || selectedSession.end_state || 'Untitled session' }}</span>
          </div>
          <span v-if="selectedSession.type === 'imported'" class="inspector-detail-badge inspector-detail-badge-neutral">Imported</span>
          <span v-if="selectedSession.comment" class="inspector-signal-ui_description">{{ selectedSession.comment }}</span>
        </div>
      </div>
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
      :highlighted-state-key="highlightedStateKey"
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
      @delete="emit('delete', selectedElement.data.id)"
    />
    <button v-if="isBehaviorContext && !readOnly" class="inspector-state-tab-add-btn" @click="emit('add-state')">+ Add state</button>
  </div>
</template>

<style scoped>
.inspector-state-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-state-tab-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-state-tab-add-btn:hover { background: #eef2f9; }

/* The read-only session card (Auto mode) — same classes as
   InspectorSignalsTab.vue/LabelProjectView.vue's own session block,
   copied here since Vue's scoped styles never cross component files. */
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; margin-top: 0.75rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-session { background: #455a64; }
.inspector-detail-badge-neutral { background: #4a6fa5; }
.inspector-signal-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-ui_description { display: block; margin-top: 0.3rem; font-size: 0.78rem; color: #666; line-height: 1.4; }
</style>
