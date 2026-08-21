<script setup>
// index.yml's dedicated editor pane — a "graph"/"code" segmented toggle over
// InspectorGraph and CodeEditor. Both segments stay mounted at once (v-show,
// not v-if): Undo/Redo act on index.yml as a whole regardless of which
// segment is showing, so CodeEditor's undo/redo state must stay live throughout.
//
// Owns no persistence of its own: EditProjectView.vue holds the unsaved-
// changes guard and decides whether a reorder click may proceed, then calls
// the endpoint and tells this view to refresh (see reload()/refresh()).
import { computed, ref } from 'vue'
import InspectorGraph from '../../../inspector/InspectorGraph.vue'
import CodeEditor from '../../../CodeEditor.vue'
import DocInfoButton from '../../../DocInfoButton.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  // Behavior branch attachment basenames — the code segment's
  // `attachments:` autocomplete offers these (see CodeEditor.vue).
  attachmentFiles: { type: Array, default: () => [] },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  // Forwarded to InspectorGraph so a selection made elsewhere (e.g. clicking
  // a row in the Inspector's Actions tab) shows up highlighted here too, not
  // just a selection made by tapping the graph itself.
  selectedElement: { type: Object, default: null }
})

const emit = defineEmits(['jump-to-definition', 'select', 'saved'])

const segment = ref('graph')
const graphRef = ref(null)
const codeEditorRef = ref(null)

function loadGraph() { return graphRef.value?.loadGraph() }
function resize() { graphRef.value?.resize() }
function fit() { graphRef.value?.fit() }
async function refresh(active) {
  await graphRef.value?.refresh(active)
}

// Re-fetches index.yml's text into the (possibly not currently visible)
// code buffer, for after an Add…/reorder changed it out from under
// whatever the editor was showing.
async function reloadCode() {
  await codeEditorRef.value?.reload()
}
const reload = reloadCode

function stateElementFor(stateKey) { return graphRef.value?.stateElementFor(stateKey) ?? null }
function actionsForState(stateKey) { return graphRef.value?.actionsForState(stateKey) ?? [] }

// Only jumps if "code" is already the visible segment — never switches
// segments itself, since Graph/Code is the user's own choice to make.
function jumpToLine(lineIndex) {
  if (segment.value !== 'code') return
  codeEditorRef.value?.jumpToLine(lineIndex)
}

// The raw YAML text and its dirty flag, for EditProjectView.vue's unsaved-
// changes guard and jump-to-definition line-finding.
const content = computed(() => codeEditorRef.value?.content ?? '')
const isDirty = computed(() => codeEditorRef.value?.isDirty ?? false)
const saving = computed(() => codeEditorRef.value?.saving ?? false)

// Delegated to the always-mounted CodeEditor instance — EditProjectView.vue's
// unsaved-changes dialog calls save()/discard() regardless of which editor
// kind is actually active.
function save() { return codeEditorRef.value?.save() }
function discard() { return codeEditorRef.value?.discard() }
function undo() { return codeEditorRef.value?.undo() }
function redo() { return codeEditorRef.value?.redo() }

defineExpose({
  loadGraph, resize, fit, refresh, reloadCode, reload, jumpToLine, stateElementFor, actionsForState,
  content, isDirty, saving, save, discard, undo, redo
})
</script>

<template>
  <div class="index-yml-editor">
    <div class="index-yml-editor-toolbar">
      <div class="index-yml-editor-segments">
        <button
          class="index-yml-editor-segment-btn"
          :class="{ 'index-yml-editor-segment-btn-active': segment === 'graph' }"
          @click="segment = 'graph'"
        >Graph</button>
        <button
          class="index-yml-editor-segment-btn"
          :class="{ 'index-yml-editor-segment-btn-active': segment === 'code' }"
          @click="segment = 'code'"
        >Code</button>
      </div>
      <div class="index-yml-editor-toolbar-actions">
        <button
          class="undo-redo-btn"
          title="Undo"
          :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.canUndo"
          @click="codeEditorRef?.undo()"
        >↺</button>
        <button
          class="undo-redo-btn"
          title="Redo"
          :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.canRedo"
          @click="codeEditorRef?.redo()"
        >↻</button>
        <button v-if="segment === 'graph'" class="fit-graph-btn" title="Fit graph to view" @click="resize(); fit()">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M9 3H5a2 2 0 0 0-2 2v4h2V5h4V3zm10 0h-4v2h4v4h2V5a2 2 0 0 0-2-2zM5 15H3v4a2 2 0 0 0 2 2h4v-2H5v-4zm14 4h-4v2h4a2 2 0 0 0 2-2v-4h-2v4z" />
          </svg>
        </button>
        <template v-if="segment === 'code'">
          <button
            class="save-btn"
            :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.isDirty"
            @click="codeEditorRef?.save()"
          >{{ codeEditorRef?.saving ? 'Saving…' : 'Save' }}</button>
          <DocInfoButton doc-name="project-specs" title="Project format specification" />
        </template>
      </div>
    </div>

    <div v-show="segment === 'graph'" class="index-yml-editor-graph">
      <InspectorGraph
        ref="graphRef"
        :project-name="projectName"
        :highlighted-state-key="highlightedStateKey"
        :auto-jump-on-highlight-change="autoJumpOnHighlightChange"
        :next-action-edge="nextActionEdge"
        :fired-action-edge="firedActionEdge"
        :selected-element="selectedElement"
        @jump-to-definition="emit('jump-to-definition', $event)"
        @select="emit('select', $event)"
      />
    </div>

    <div v-show="segment === 'code'" class="index-yml-editor-code">
      <CodeEditor
        ref="codeEditorRef"
        :project-name="projectName"
        file-name="index.yml"
        :yaml-attachment-files="attachmentFiles"
        @saved="emit('saved', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.index-yml-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.index-yml-editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.index-yml-editor-segments { display: flex; gap: 0.2rem; padding: 0.2rem; border-radius: 8px; background: #eef1f5; }
.index-yml-editor-segment-btn { padding: 0.3rem 0.8rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.82rem; color: #555; }
.index-yml-editor-segment-btn-active { background: white; color: #2c4d7a; font-weight: 600; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12); }
.index-yml-editor-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.fit-graph-btn { display: flex; align-items: center; justify-content: center; width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 6px; border: 1px solid #ccc; background: white; color: #555; cursor: pointer; }
.fit-graph-btn:hover { background: #eef2f9; border-color: #4a6fa5; color: #4a6fa5; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.index-yml-editor-graph, .index-yml-editor-code { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0.75rem; }
</style>
