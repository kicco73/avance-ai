<script setup>
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
  currentFileName: { type: String, default: null },
  deletingFile: { type: String, default: null },
  renamingFile: { type: String, default: null },
})

const emit = defineEmits(['set-project-field', 'set-service-level', 'delete-file', 'rename-file'])

const showFileCard = computed(() => (
  props.selectedSource == null && !props.sourcesRootSelected
  && props.currentFileName != null && props.currentFileName !== 'index.yml'
))

const projectMetadata = ref(null)

async function refresh() {
  try {
    projectMetadata.value = (await getProjectMetadata(props.projectId)).project
  } catch {
  }
}

defineExpose({ refresh, resync: refresh })

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
