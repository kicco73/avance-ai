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
import { ref, watch } from 'vue'
import InspectorDetailCard from './InspectorDetailCard.vue'

const props = defineProps({
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
  // See EditProjectView.vue's own docstring on this — 'state:<key>' while
  // `selectedElement` is the state a "+ Add state" click just created,
  // null otherwise.
  recentlyAddedKey: { type: String, default: null }
})

const emit = defineEmits(['select', 'select-attachment', 'jump-to-attachment', 'set-field', 'delete', 'add-state'])

// This tab only ever has the one card, but it's still the parent that
// owns open/closed now (see InspectorDetailCard.vue's own `open` prop) —
// closed whenever the selection moves to a genuinely different state,
// same as before, *except* when it moved there because "+ Add state" just
// created it — that one opens straight into its own edit form instead of
// requiring a second click.
const open = ref(false)
watch(() => props.selectedElement?.data.id, (id) => {
  open.value = id != null && props.recentlyAddedKey === `state:${id}`
})
</script>

<template>
  <div class="inspector-state-tab">
    <p v-if="!selectedElement" class="inspector-state-tab-empty">No state selected.</p>
    <InspectorDetailCard
      v-else
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
.inspector-state-tab-empty { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-state-tab-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-state-tab-add-btn:hover { background: #eef2f9; }
</style>
