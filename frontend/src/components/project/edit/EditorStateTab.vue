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
import { onMounted, ref, watch } from 'vue'
import { getProjectMetadata } from '../../../api.js'
import InspectorProjectCard from '../../inspector/InspectorProjectCard.vue'
import InspectorStateTab from '../../inspector/InspectorStateTab.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  projectId: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  selectedSource: { type: Object, default: null },
  sourcesRootSelected: { type: Boolean, default: false },
})

const emit = defineEmits(['set-project-field', 'set-service-level'])

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
  <InspectorStateTab
    v-bind="$attrs"
    :project-id="projectId"
    :selected-element="selectedElement"
    :selected-source="selectedSource"
    :sources-root-selected="sourcesRootSelected"
  />
</template>
