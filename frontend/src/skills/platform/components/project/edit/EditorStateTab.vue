<script setup>
// The editor's own State tab: the project card, plus the state/action
// detail everyone else reads too.
//
// It exists so that InspectorStateTab does not have to decide whether it
// is editing. The project card is the only thing in that tab that belongs
// to the editor alone — it is the one card that writes project-level
// fields, and its data was the tab's only reason to call a platform
// route. That route is why a second screen could not mount the tab
// without dragging the editor behind it; see InspectorStateTab's own
// `readOnly` note for the rest.
import { computed, onMounted, ref, watch } from 'vue'
import { getProjectMetadata } from '../../../api.js'
import InspectorFileCard from '../../inspector/InspectorFileCard.vue'
import InspectorProjectCard from '../../inspector/InspectorProjectCard.vue'
import InspectorStateTab from '../../inspector/InspectorStateTab.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  projectId: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  selectedSource: { type: Object, default: null },
  sourcesRootSelected: { type: Boolean, default: false },
  // Design mode's currently open file — only the editor ever browses one,
  // which is why the card that shows it lives here and not in the tab.
  currentFileName: { type: String, default: null },
  deletingFile: { type: String, default: null },
  renamingFile: { type: String, default: null },
})

const emit = defineEmits(['set-project-field', 'set-service-level', 'delete-file', 'rename-file'])

// The same condition the tab used to apply: a file card only where there
// is a file being browsed that is not index.yml, and no source has taken
// the panel over.
const showFileCard = computed(() => (
  props.selectedSource == null && !props.sourcesRootSelected
  && props.currentFileName != null && props.currentFileName !== 'index.yml'
))

const projectMetadata = ref(null)

// Inspector.vue's own registerTab dispatch — same "reload on demand, the
// shell never knows why" contract every other self-fetching tab
// implements. It moved here with the fetch.
async function refresh() {
  try {
    projectMetadata.value = (await getProjectMetadata(props.projectId)).project
  } catch {
    // already surfaced via apiFetch
  }
}

defineExpose({ refresh })

onMounted(refresh)
watch(() => props.projectId, refresh)
</script>

<template>
  <InspectorProjectCard
    v-if="projectMetadata && !selectedElement && !selectedSource && !sourcesRootSelected"
    :project="projectMetadata"
    :editable="true"
    @set-field="(field, value) => emit('set-project-field', field, value)"
    @set-service-level="(service, level) => emit('set-service-level', service, level)"
  />
  <InspectorFileCard
    v-if="showFileCard"
    :project-id="projectId"
    :file-name="currentFileName"
    :deleting="deletingFile === currentFileName"
    :renaming="renamingFile === currentFileName"
    @delete="emit('delete-file', currentFileName)"
    @rename="(newBasename) => emit('rename-file', currentFileName, newBasename)"
  />
  <InspectorStateTab
    v-bind="$attrs"
    :project-id="projectId"
    :selected-element="selectedElement"
    :selected-source="selectedSource"
    :sources-root-selected="sourcesRootSelected"
    :current-file-name="currentFileName"
  />
</template>
