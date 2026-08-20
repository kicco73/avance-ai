<script setup>
// The Inspector's own "Info" tab (shown instead of "States" while
// EditProjectView.vue's editorOpen is on — see its own inspectorTabs) —
// the project's own id/ui-label/ui-description (see InspectorProjectCard.
// vue) always on top, followed by the same read-only detail card "States"
// already shows for whichever state the *shared* Graph selection resolves
// to (itself, or the one containing a selected action — see
// EditProjectView.vue's own stateTabElement, resolved via
// IndexYmlEditorPanel's stateElementFor). This component owns none of
// that state resolution itself, purely a thin wrapper so the card is
// clickable here (see selectable below) without making it clickable in
// "States" too — it does, however, own the project card's own fetch
// (self-contained the same way InspectorSignalsTab.vue/
// InspectorEnvKeysTab.vue each own their own list's fetch), since nothing
// about it is graph-selection-driven the way `selectedElement` is.
import { onMounted, ref, watch } from 'vue'
import { getProjectMetadata } from '../../api.js'
import InspectorDetailCard from './InspectorDetailCard.vue'
import InspectorProjectCard from './InspectorProjectCard.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
  // See EditProjectView.vue's own docstring on this — 'state:<key>' while
  // `selectedElement` is the state a "+ Add state" click just created,
  // null otherwise.
  recentlyAddedKey: { type: String, default: null }
})

const emit = defineEmits([
  'select', 'select-attachment', 'jump-to-attachment', 'set-field', 'set-project-field', 'delete', 'add-state'
])

// This tab only ever has the one state card, but it's still the parent
// that owns open/closed now (see InspectorDetailCard.vue's own `open`
// prop) — closed whenever the selection moves to a genuinely different
// state, same as before, *except* when it moved there because "+ Add
// state" just created it — that one opens straight into its own edit form
// instead of requiring a second click.
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

// Inspector.vue's own registerTab dispatch (see its own refresh()) —
// same "reload on demand, the shell never knows why" contract every other
// self-fetching tab (InspectorSignalsTab.vue/InspectorEnvKeysTab.vue)
// already implements.
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
    <InspectorDetailCard
      v-if="selectedElement"
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
    <button class="inspector-state-tab-add-btn" @click="emit('add-state')">+ Add state</button>
  </div>
</template>

<style scoped>
.inspector-state-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-state-tab-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-state-tab-add-btn:hover { background: #eef2f9; }
</style>
