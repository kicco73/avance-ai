<script setup>
// Composes InspectorGraph.vue (the graph) and InspectorDetailCard.vue (the
// read-only card for whatever's selected) for the "States" tab, holding the
// shared `selectedElement` that Graph emits and Card reads.
import { nextTick, ref } from 'vue'
import InspectorGraph from './InspectorGraph.vue'
import InspectorDetailCard from './InspectorDetailCard.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  firedActionEdge: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  // See InspectorGraph.vue's own imported prop docstring.
  imported: { type: Boolean, default: false },
  // See InspectorGraph.vue's own sessionId prop docstring.
  sessionId: { type: [Number, String], default: null }
})

const emit = defineEmits(['jump-to-definition', 'select-attachment', 'update-expected-state'])

const graphRef = ref(null)
const selectedElement = ref(null)

// Closing/opening the detail card changes how much height the graph container
// has — a cytoscape canvas doesn't pick that up on its own, so every selection
// change nudges it to resize.
function handleSelect(element) {
  selectedElement.value = element
  nextTick(() => graphRef.value?.resize())
}

function closeDetail() {
  handleSelect(null)
}

function loadGraph() { return graphRef.value?.loadGraph() }
function resize() { graphRef.value?.resize() }
function fit() { graphRef.value?.fit() }
function refresh(active) { return graphRef.value?.refresh(active) }
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
      :project-name="projectName"
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
      @select-attachment="emit('select-attachment', $event)"
      @close="closeDetail"
    />
  </div>
</template>

<style scoped>
.inspector-graph-tab { flex: 1; display: flex; flex-direction: column; min-height: 0; }
</style>
