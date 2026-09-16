<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import InspectorGraph from './InspectorGraph.vue'
import InspectorDetailCard from '../../../../components/skillkit/InspectorDetailCard.vue'
import SourcesEditDialog from './SourcesEditDialog.vue'
import { customDialog } from '../../../../dialogStore.js'

const props = defineProps({
  projectId: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  firedActionEdge: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  sources: { type: Array, default: () => [] },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  imported: { type: Boolean, default: false },
  sessionId: { type: [Number, String], default: null },
  editable: { type: Boolean, default: false },
  availableStates: { type: Array, default: () => [] },
  recentlyAddedKey: { type: String, default: null },
  stateTokens: { type: Number, default: null },
  saveField: { type: Function, default: null }
})

const emit = defineEmits([
  'jump-to-definition', 'select-attachment', 'jump-to-attachment', 'update-expected-state',
  'select', 'set-field', 'delete', 'open-actions-order'
])

const graphRef = ref(null)
const selectedElement = ref(null)
const open = ref(false)

function openSourcesDialog() {
  customDialog({
    component: SourcesEditDialog,
    props: {
      sources: props.sources.map((entry) => entry.source),
      stateData: selectedElement.value.data,
      onSetField: (field, value) => emit('set-field', field, value)
    }
  })
}

const elementIdentity = computed(() => {
  const el = selectedElement.value
  if (!el) return null
  return el.kind === 'state' ? `state:${el.data.id}` : `action:${el.data.matchStateKey}/${el.data.actionName}`
})

watch(elementIdentity, (identity) => {
  open.value = identity != null && props.recentlyAddedKey === identity
})

function handleSelect(element) {
  selectedElement.value = element
  emit('select', element)
  nextTick(() => graphRef.value?.resize())
}

function resyncSelection() {
  const el = selectedElement.value
  if (!el) return
  selectedElement.value = el.kind === 'state'
    ? stateElementFor(el.data.id)
    : (graphRef.value?.actionsForState(el.data.matchStateKey) ?? []).find(
        (action) => action.data.actionName === el.data.actionName
      ) ?? null
}

function closeDetail() {
  handleSelect(null)
}

function loadGraph() { return graphRef.value?.loadGraph() }
function resize() { graphRef.value?.resize() }
function fit() { graphRef.value?.fit() }
async function refresh(active) {
  const result = await graphRef.value?.refresh(active)
  resyncSelection()
  return result
}
function stateElementFor(stateKey) { return graphRef.value?.stateElementFor(stateKey) ?? null }

defineExpose({ loadGraph, resize, fit, refresh, resync: refresh, stateElementFor })
</script>

<template>
  <div class="inspector-graph-tab">
    <InspectorGraph
      ref="graphRef"
      :project-id="projectId"
      :highlighted-state-key="highlightedStateKey"
      :auto-jump-on-highlight-change="autoJumpOnHighlightChange"
      :fired-action-edge="firedActionEdge"
      :annotatable="annotatable"
      :expected-state="expectedState"
      :imported="imported"
      :session-id="sessionId"
      @jump-to-definition="emit('jump-to-definition', $event)"
      @update-expected-state="emit('update-expected-state', $event)"
      @select="handleSelect"
    />

    <InspectorDetailCard
      :selected-element="selectedElement"
      :editable-files="editableFiles"
      :fired-action-edge="firedActionEdge"
      :highlighted-state-key="highlightedStateKey"
      :editable="editable"
      :available-states="availableStates"
      :recently-added-key="recentlyAddedKey"
      :state-tokens="stateTokens"
      :save-field="saveField"
      :open="open"
      @update:open="open = $event"
      @select-attachment="emit('select-attachment', $event)"
      @jump-to-attachment="emit('jump-to-attachment', $event)"
      @set-field="(field, value) => emit('set-field', field, value)"
      @delete="emit('delete', selectedElement)"
      @open-actions-order="emit('open-actions-order', selectedElement)"
      @open-sources="openSourcesDialog"
      @close="closeDetail"
    />
  </div>
</template>

<style scoped>
.inspector-graph-tab { flex: 1; display: flex; flex-direction: column; min-height: 0; }
</style>
