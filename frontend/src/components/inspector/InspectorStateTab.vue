<script setup>
// The Inspector's own "State" tab (shown instead of "States" while
// EditProjectView.vue's editorOpen is on — see its own inspectorTabs) —
// the same read-only detail card "States" already shows, just for
// whichever state the *shared* Graph selection resolves to (itself, or
// the one containing a selected action — see EditProjectView.vue's own
// stateTabElement, resolved via IndexYmlEditorView's stateElementFor).
// This component owns none of that resolution itself, purely a thin
// wrapper so the card is clickable here (see selectable below) without
// making it clickable in "States" too.
import InspectorDetailCard from './InspectorDetailCard.vue'

const props = defineProps({
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null }
})

const emit = defineEmits(['select', 'select-attachment'])
</script>

<template>
  <div class="inspector-state-tab">
    <p v-if="!selectedElement" class="inspector-state-tab-empty">No state selected.</p>
    <InspectorDetailCard
      v-else
      :selected-element="selectedElement"
      :editable-files="editableFiles"
      :highlighted-state-key="highlightedStateKey"
      selectable
      @select="emit('select', selectedElement)"
      @select-attachment="emit('select-attachment', $event)"
      @close="emit('select', null)"
    />
  </div>
</template>

<style scoped>
.inspector-state-tab { flex: 1; min-height: 0; overflow-y: auto; }
.inspector-state-tab-empty { margin: 0; color: #444; font-size: 0.9rem; }
</style>
