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
import { textareaDialog } from '../../../../dialogStore.js'
import { aiEditIndexYml } from '../../../../api.js'

const props = defineProps({
  projectName: { type: String, required: true },
  // Behavior branch attachment basenames — the code segment's
  // `attachments:` autocomplete offers these (see CodeEditor.vue).
  attachmentFiles: { type: Array, default: () => [] },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
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
const aiEditing = ref(false)

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

// The toolbar's AI button: prompts for a free-form problem/change
// description, sends it (with the project's current index.yml and the
// format spec) to the backend's AiService, drops the rewritten content
// into the code buffer and saves it — Graph/Code stays whatever the user
// had selected (see jumpToLine's docstring above). CodeEditor stays
// mounted regardless of which segment is showing, so setContent/save
// work even when the graph segment is the one currently visible.
async function aiEdit() {
  const instruction = await textareaDialog({
    title: 'AI-assisted edit',
    body: 'Describe the problem to solve or the change to make in index.yml.',
    placeholder: 'e.g. Add a state where the user can request a refund…'
  })
  if (!instruction) return
  aiEditing.value = true
  try {
    const result = await aiEditIndexYml(props.projectName, instruction)
    codeEditorRef.value?.setContent(result.content)
    await codeEditorRef.value?.save()
  } catch {
    // already surfaced via apiFetch's shared error store
  } finally {
    aiEditing.value = false
  }
}

defineExpose({
  loadGraph, resize, fit, refresh, reloadCode, reload, jumpToLine, stateElementFor, actionsForState,
  content, isDirty, saving, save, discard, undo, redo
})
</script>

<template>
  <div class="index-yml-editor">
    <div class="index-yml-editor-toolbar">
      <div class="index-yml-editor-toolbar-left">
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
        <button
          class="ai-edit-btn"
          :class="{ 'ai-edit-btn-loading': aiEditing }"
          :title="aiEditing ? 'Generating…' : 'AI-assisted edit'"
          :disabled="aiEditing || codeEditorRef?.loading || codeEditorRef?.saving"
          @click="aiEdit"
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
          </svg>
        </button>
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
.index-yml-editor-toolbar-left { display: flex; align-items: center; gap: 0.5rem; }
.index-yml-editor-segments { display: flex; gap: 0.2rem; padding: 0.2rem; border-radius: 8px; background: #eef1f5; }
.index-yml-editor-segment-btn { padding: 0.3rem 0.8rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.82rem; color: #555; }
.index-yml-editor-segment-btn-active { background: white; color: #2c4d7a; font-weight: 600; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12); }
.index-yml-editor-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; }
.ai-edit-btn { display: flex; align-items: center; justify-content: center; width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 6px; border: 1px solid #ccc; background: white; color: #8b5cf6; cursor: pointer; }
.ai-edit-btn:hover:not(:disabled) { background: #f5f0fe; border-color: #8b5cf6; }
.ai-edit-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ai-edit-btn-loading svg { animation: ai-edit-btn-spin 1.1s linear infinite; }
@keyframes ai-edit-btn-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.fit-graph-btn { display: flex; align-items: center; justify-content: center; width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 6px; border: 1px solid #ccc; background: white; color: #555; cursor: pointer; }
.fit-graph-btn:hover { background: #eef2f9; border-color: #4a6fa5; color: #4a6fa5; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.index-yml-editor-graph, .index-yml-editor-code { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0.75rem; }
</style>
