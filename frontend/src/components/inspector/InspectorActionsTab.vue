<script setup>
// The Inspector's own "Actions" tab (shown alongside "State" while
// EditProjectView.vue's editorOpen is on — see its own inspectorTabs) —
// every action of the currently-selected state, each as the same
// read-only detail card "States" already shows for one, one per row,
// reorderable via native HTML5 drag-and-drop. Owns none of the actual
// persistence itself: a drop only ever emits 'reorder' with the plain
// {actionName, position} intent — EditProjectView.vue is the one that
// knows the containing state's own key, calls the actual endpoint, and
// coordinates refreshing the graph/code buffer afterward.
import { ref } from 'vue'
import InspectorDetailCard from './InspectorDetailCard.vue'

const props = defineProps({
  actions: { type: Array, default: () => [] }, // [{ kind: 'action', data }]
  editableFiles: { type: Array, default: null },
  selectedElement: { type: Object, default: null },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null }
})

const emit = defineEmits(['select', 'select-attachment', 'reorder'])

const draggedIndex = ref(null)
const dragOverIndex = ref(null)

function isSelected(action) {
  return props.selectedElement?.kind === 'action' && props.selectedElement.data.actionName === action.data.actionName
}

function handleDragStart(index) {
  draggedIndex.value = index
}

function handleDragOver(index) {
  dragOverIndex.value = index
}

function handleDragLeave(index) {
  if (dragOverIndex.value === index) dragOverIndex.value = null
}

function handleDrop(index) {
  const fromIndex = draggedIndex.value
  draggedIndex.value = null
  dragOverIndex.value = null
  if (fromIndex === null || fromIndex === index) return
  const actionName = props.actions[fromIndex]?.data.actionName
  if (actionName == null) return
  emit('reorder', { actionName, position: index })
}

function handleDragEnd() {
  draggedIndex.value = null
  dragOverIndex.value = null
}
</script>

<template>
  <div class="inspector-actions-tab">
    <p v-if="!actions.length" class="inspector-actions-tab-empty">This state has no actions.</p>
    <div
      v-for="(action, index) in actions"
      :key="action.data.actionName"
      class="inspector-actions-tab-row"
      :class="{ 'inspector-actions-tab-row-drag-over': dragOverIndex === index && draggedIndex !== index }"
      draggable="true"
      @dragstart="handleDragStart(index)"
      @dragover.prevent="handleDragOver(index)"
      @dragleave="handleDragLeave(index)"
      @drop.prevent="handleDrop(index)"
      @dragend="handleDragEnd"
    >
      <span class="inspector-actions-tab-drag-handle" title="Drag to reorder">⠿</span>
      <InspectorDetailCard
        class="inspector-actions-tab-card"
        :selected-element="action"
        :editable-files="editableFiles"
        :next-action-edge="nextActionEdge"
        :fired-action-edge="firedActionEdge"
        :highlighted-state-key="highlightedStateKey"
        :selectable="!isSelected(action)"
        :closable="false"
        @select="emit('select', action)"
        @select-attachment="emit('select-attachment', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.inspector-actions-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0.5rem; }
.inspector-actions-tab-empty { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-actions-tab-row { display: flex; align-items: stretch; gap: 0.3rem; }
.inspector-actions-tab-row-drag-over { outline: 2px dashed #4a6fa5; outline-offset: 2px; border-radius: 8px; }
.inspector-actions-tab-drag-handle { flex-shrink: 0; display: flex; align-items: center; cursor: grab; color: #999; font-size: 0.9rem; padding: 0 0.2rem; user-select: none; }
.inspector-actions-tab-card { flex: 1; min-width: 0; margin-top: 0 !important; }
</style>
