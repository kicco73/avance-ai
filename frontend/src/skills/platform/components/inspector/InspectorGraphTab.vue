<script setup>
// Composes InspectorGraph.vue (the graph) and InspectorDetailCard.vue (the
// read-only card for whatever's selected) for the "States" tab, holding the
// shared `selectedElement` that Graph emits and Card reads.
import { computed, nextTick, ref, watch } from 'vue'
import InspectorGraph from './InspectorGraph.vue'
import InspectorDetailCard from '../skillkit/InspectorDetailCard.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  firedActionEdge: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  // See InspectorGraph.vue's own imported prop docstring.
  imported: { type: Boolean, default: false },
  // See InspectorGraph.vue's own sessionId prop docstring.
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

const elementIdentity = computed(() => {
  const el = selectedElement.value
  if (!el) return null
  return el.kind === 'state' ? `state:${el.data.id}` : `action:${el.data.matchStateKey}/${el.data.actionName}`
})

watch(elementIdentity, (identity) => {
  open.value = identity != null && props.recentlyAddedKey === identity
})

// Closing/opening the detail card changes how much height the graph container
// has — a cytoscape canvas doesn't pick that up on its own, so every selection
// change nudges it to resize.
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
// Straight pass-through to InspectorGraph.vue's own lookup — lets a caller get
// a specific state's read-only card data without it becoming the Graph's actual
// selection (e.g. showing a session's start/end state in their own dedicated cards).
function stateElementFor(stateKey) { return graphRef.value?.stateElementFor(stateKey) ?? null }

defineExpose({ loadGraph, resize, fit, refresh, stateElementFor })
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
      @close="closeDetail"
    />
  </div>
</template>

<style scoped>
.inspector-graph-tab { flex: 1; display: flex; flex-direction: column; min-height: 0; }
</style>
