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
  deletingFile: { type: String, default: null }
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
      :project="projectMetadata"
      editable
      @set-field="(field, value) => emit('set-project-field', field, value)"
    />
    <InspectorFileCard
      v-if="showFileCard"
      :file-name="currentFileName"
      :deleting="deletingFile === currentFileName"
      @delete="emit('delete-file', currentFileName)"
    />
    <InspectorDetailCard
      v-if="selectedElement && isBehaviorContext"
      :selected-element="selectedElement"
      :editable-files="editableFiles"
      :highlighted-state-key="highlightedStateKey"
      :recently-added-key="recentlyAddedKey"
      selectable
      editable
      :closable="false"
      :open="open"
      @update:open="open = $event"
      @select="emit('select', selectedElement)"
      @select-attachment="emit('select-attachment', $event)"
      @jump-to-attachment="emit('jump-to-attachment', $event)"
      @set-field="(field, value) => emit('set-field', field, value)"
      @delete="emit('delete', selectedElement.data.id)"
    />
    <button v-if="isBehaviorContext" class="inspector-state-tab-add-btn" @click="emit('add-state')">+ Add state</button>
  </div>
</template>

<style scoped>
.inspector-state-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-state-tab-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-state-tab-add-btn:hover { background: #eef2f9; }
</style>
