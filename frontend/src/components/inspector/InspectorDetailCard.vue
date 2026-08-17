<script setup>
// The read-only badge/fields/attachments card for whatever's selected in
// the Graph (a state or an action) — extracted out of InspectorGraphTab.vue
// so InspectorGraphTab.vue can compose this alongside InspectorGraph.vue
// (see that component's own docstring) instead of owning both concerns
// itself. Purely a function of `selectedElement` — every "is this the
// current/next/fired one" badge computed here from props, never from
// internal state, so a parent can drive this from Graph's own emitted
// selection without this component needing any cytoscape awareness at all.
import { computed } from 'vue'

const props = defineProps({
  selectedElement: { type: Object, default: null }, // { kind: 'state' | 'action', data } | null
  editableFiles: { type: Array, default: null },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null },
  // Whether clicking the card's own body (not the × close button, not an
  // attachment button) emits 'select' — off by default, since the
  // original "States" tab usage (InspectorGraphTab.vue) has nothing
  // useful to do with a click on a card that's already the Graph's own
  // selection. The Inspector's "State"/"Actions" tabs turn this on: there
  // the same card can represent an element that isn't the literal shared
  // selection yet (e.g. "State" showing the state an already-selected
  // action merely belongs to) — clicking it promotes it to become that
  // selection, same as clicking it directly in the Graph would.
  selectable: { type: Boolean, default: false },
  // The × close button — on by default (the original floating-card
  // usage, and "State"). Off for a row inside "Actions"' own list: there
  // every action is always shown, nothing for × to "close" without also
  // meaning delete (a distinct, separate action of its own).
  closable: { type: Boolean, default: true }
})

const emit = defineEmits(['select-attachment', 'close', 'select'])

function handleCardClick() {
  if (props.selectable) emit('select')
}

function attachmentLabel(index) { return String.fromCharCode(97 + index) }

const isSelectedActionNext = computed(() => {
  if (props.selectedElement?.kind !== 'action' || !props.nextActionEdge) return false
  return (
    props.selectedElement.data.matchStateKey === props.nextActionEdge.stateKey &&
    props.selectedElement.data.actionName === props.nextActionEdge.actionName
  )
})

const isSelectedActionFired = computed(() => {
  if (props.selectedElement?.kind !== 'action' || !props.firedActionEdge) return false
  return (
    props.selectedElement.data.matchStateKey === props.firedActionEdge.stateKey &&
    props.selectedElement.data.actionName === props.firedActionEdge.actionName
  )
})

const isSelectedStateCurrent = computed(() => {
  return props.selectedElement?.kind === 'state' && props.selectedElement.data.id === props.highlightedStateKey
})

const hasSelectedElementBadges = computed(() => {
  if (!props.selectedElement) return false
  if (props.selectedElement.kind === 'state') {
    const d = props.selectedElement.data
    return isSelectedStateCurrent.value || d.isStart || d.final || !d.chat || d.historyCutoff
  }
  const d = props.selectedElement.data
  return isSelectedActionNext.value || isSelectedActionFired.value || !d.hasTrigger || d.isInitEdge
})

function selectAttachment(fileName) { emit('select-attachment', fileName) }
</script>

<template>
  <div
    v-if="selectedElement"
    class="inspector-detail-card"
    :class="{ 'inspector-detail-card-selectable': selectable }"
    @click="handleCardClick"
  >
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span
          class="inspector-detail-badge"
          :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'"
        >{{ selectedElement.kind === 'state' ? 'State' : 'Action' }}</span>
        <span class="inspector-detail-title">{{ selectedElement.data.uiLabel }}</span>
        <button v-if="closable" class="close-x-btn" title="Close" @click.stop="emit('close')">×</button>
      </div>
      <div v-if="hasSelectedElementBadges" class="inspector-detail-badges">
        <template v-if="selectedElement.kind === 'state'">
          <span v-if="isSelectedStateCurrent" class="inspector-detail-badge inspector-detail-badge-current">Current</span>
          <span v-if="selectedElement.data.isStart" class="inspector-detail-badge inspector-detail-badge-start">Start</span>
          <span v-if="selectedElement.data.final" class="inspector-detail-badge inspector-detail-badge-final">Final</span>
          <span v-if="!selectedElement.data.chat" class="inspector-detail-badge inspector-detail-badge-neutral">No chat</span>
          <span v-if="selectedElement.data.historyCutoff" class="inspector-detail-badge inspector-detail-badge-neutral">History cutoff</span>
        </template>
        <template v-else>
          <span v-if="selectedElement.data.isInitEdge" class="inspector-detail-badge inspector-detail-badge-start">Start</span>
          <span v-if="isSelectedActionNext" class="inspector-detail-badge inspector-detail-badge-next">Next</span>
          <span v-if="isSelectedActionFired" class="inspector-detail-badge inspector-detail-badge-fired">Fired</span>
          <span v-if="!selectedElement.data.hasTrigger" class="inspector-detail-badge inspector-detail-badge-manual">Manual</span>
        </template>
      </div>
    </div>
    <div class="inspector-detail-body">
      <template v-if="selectedElement.kind === 'state'">
        <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
      </template>
      <template v-else>
        <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
        <p class="inspector-detail-field"><template v-if="!selectedElement.data.isInitEdge"><strong>{{ selectedElement.data.source }}</strong> → </template><strong>{{ selectedElement.data.target }}</strong></p>
        <p v-if="selectedElement.data.buttonText" class="inspector-detail-field"><strong>Button:</strong> {{ selectedElement.data.buttonText }}</p>
        <p v-if="selectedElement.data.trigger" class="inspector-detail-field"><strong>Trigger:</strong><code class="inspector-detail-code">{{ selectedElement.data.trigger }}</code></p>
        <p v-if="selectedElement.data.actionPrompt" class="inspector-detail-field"><strong>Action prompt:</strong> {{ selectedElement.data.actionPrompt }}</p>
        <p v-if="selectedElement.data.onEnter" class="inspector-detail-field"><strong>On enter:</strong> {{ selectedElement.data.onEnter }}</p>
      </template>
      <div v-if="editableFiles && selectedElement.data.attachments?.length" class="inspector-attachments">
        <button
          v-for="(fileName, idx) in selectedElement.data.attachments"
          :key="fileName"
          class="inspector-attachment-btn"
          :class="{ 'inspector-attachment-btn-disabled': !editableFiles.includes(fileName) }"
          :disabled="!editableFiles.includes(fileName)"
          :title="editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
          @click.stop="selectAttachment(fileName)"
        >{{ attachmentLabel(idx) }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
.inspector-detail-card-selectable { cursor: pointer; }
.inspector-detail-card-selectable:hover { border-color: #c9d6e8; background: #f0f4fa; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-state { background: #4a6fa5; }
.inspector-detail-badge-action { background: #8a6d3b; }
.inspector-detail-badge-current { background: #f5a623; color: #3a2600; }
.inspector-detail-badge-start, .inspector-detail-badge-next { background: #2e7d32; }
.inspector-detail-badge-fired { background: #ad1457; }
.inspector-detail-badge-final { background: #c62828; }
.inspector-detail-badge-manual { background: #5c6b7a; }
.inspector-detail-badge-neutral { background: #8a8a8a; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.close-x-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.close-x-btn:hover { background: #eee; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0 0 0.5rem; line-height: 1.4; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; }
.inspector-detail-code { font-size: 0.75rem; background: #eee; border-radius: 4px; padding: 0.1rem 0.4rem; }
.inspector-attachments { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.5rem; }
.inspector-attachment-btn { width: 1.5rem; height: 1.5rem; line-height: 1; border-radius: 4px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.72rem; font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.inspector-attachment-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.inspector-attachment-btn-disabled { border-color: #ccc; color: #aaa; cursor: not-allowed; }
.inspector-attachment-btn-disabled:hover { background: white; color: #aaa; }
</style>
