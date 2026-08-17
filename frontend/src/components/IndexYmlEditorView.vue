<script setup>
// index.yml's own dedicated editor pane — a "graph"/"code" segmented
// toggle over the same two building blocks used elsewhere: InspectorGraph
// (the same graph Inspector.vue's own "States" tab uses when editorOpen
// is off — see EditProjectView.vue's own inspectorTabs) and CodeEditor
// (the same one every other project file uses). Both segments stay
// mounted at once (v-show, not v-if): Undo/Redo act on index.yml as a
// whole regardless of which segment is currently showing, so CodeEditor's
// own undo()/redo()/canUndo/canRedo need to stay live even while "graph"
// is the one actually visible.
//
// Owns no persistence of its own for Add…/reorder — see the emitted
// add-state/add-signal/add-action events below: EditProjectView.vue is
// the one holding the unsaved-changes guard, so it's the one that must
// decide whether a click here is even allowed to proceed, then call the
// actual endpoint, then tell this view to refresh (see reload()/refresh()).
import { computed, ref } from 'vue'
import InspectorGraph from './inspector/InspectorGraph.vue'
import CodeEditor from './CodeEditor.vue'
import DocInfoButton from './DocInfoButton.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  // The state "Add action" would add to — null disables that menu item
  // (see EditProjectView.vue's own selectedGraphElement, the shared
  // Graph/State-tab/Actions-tab selection this is derived from).
  selectedStateKey: { type: String, default: null }
})

const emit = defineEmits(['jump-to-definition', 'select', 'saved', 'add-state', 'add-signal', 'add-action'])

const segment = ref('graph')
const graphRef = ref(null)
const codeEditorRef = ref(null)
const addMenuOpen = ref(false)

function loadGraph() { return graphRef.value?.loadGraph() }
function resize() { graphRef.value?.resize() }
function fit() { graphRef.value?.fit() }
async function refresh(active) {
  await graphRef.value?.refresh(active)
}

// Re-fetches index.yml's own text into the (possibly not currently
// visible) code buffer — for after an Add…/reorder just changed it out
// from under whatever the editor was showing (see this component's own
// docstring: the actual write always happens one level up).
async function reloadCode() {
  await codeEditorRef.value?.reload()
}

function stateElementFor(stateKey) { return graphRef.value?.stateElementFor(stateKey) ?? null }
function actionsForState(stateKey) { return graphRef.value?.actionsForState(stateKey) ?? [] }

// The caller's own entry point for a Graph click / a Signals-tab-style
// row click elsewhere in the app (see EditProjectView.vue's own
// jumpToDefinition) — switches to "code" so the cursor move is actually
// visible, same as clicking node/edge already does inside InspectorGraph
// itself (see its own emit('jump-to-definition', ...), forwarded
// straight through below without this view acting on it directly).
function jumpToLine(lineIndex) {
  segment.value = 'code'
  codeEditorRef.value?.jumpToLine(lineIndex)
}

function clickAdd(kind) {
  addMenuOpen.value = false
  emit(`add-${kind}`)
}

// The raw YAML text and its own dirty flag — for EditProjectView.vue's
// own unsaved-changes guard (before Add…/reorder) and jump-to-definition
// line-finding (see findStateLine/findActionLine/findSignalLine there),
// neither of which this view has any business knowing about itself.
const content = computed(() => codeEditorRef.value?.content ?? '')
const isDirty = computed(() => codeEditorRef.value?.isDirty ?? false)

// Delegated straight to the (always-mounted, see this component's own
// docstring) CodeEditor instance — EditProjectView.vue's own switch-file
// dialog calls save() regardless of which kind of editor is actually
// active (see its own activeEditor() helper), and Undo/Redo already
// live in this view's own toolbar above, but a caller may still want
// them directly (e.g. a future keyboard shortcut scoped to this view).
function save() { return codeEditorRef.value?.save() }
function undo() { return codeEditorRef.value?.undo() }
function redo() { return codeEditorRef.value?.redo() }

defineExpose({
  loadGraph, resize, fit, refresh, reloadCode, jumpToLine, stateElementFor, actionsForState, content, isDirty,
  save, undo, redo
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
        <div class="index-yml-editor-add-menu">
          <button class="add-btn" @click="addMenuOpen = !addMenuOpen">Add…</button>
          <div v-if="addMenuOpen" class="index-yml-editor-add-panel">
            <button class="index-yml-editor-add-item" @click="clickAdd('state')">Add state</button>
            <button
              class="index-yml-editor-add-item"
              :disabled="!selectedStateKey"
              :title="selectedStateKey ? '' : 'Select a state first'"
              @click="clickAdd('action')"
            >Add action</button>
            <button class="index-yml-editor-add-item" @click="clickAdd('signal')">Add signal</button>
          </div>
        </div>
        <button
          class="undo-redo-btn"
          title="Undo"
          :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.canUndo"
          @click="codeEditorRef?.undo()"
        >↶</button>
        <button
          class="undo-redo-btn"
          title="Redo"
          :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.canRedo"
          @click="codeEditorRef?.redo()"
        >↷</button>
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
        @jump-to-definition="emit('jump-to-definition', $event)"
        @select="emit('select', $event)"
      />
    </div>

    <div v-show="segment === 'code'" class="index-yml-editor-code">
      <CodeEditor
        ref="codeEditorRef"
        :project-name="projectName"
        file-name="index.yml"
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
.index-yml-editor-add-menu { position: relative; }
.add-btn { padding: 0.4rem 0.9rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.85rem; }
.add-btn:hover { background: #eef2f9; }
.index-yml-editor-add-panel { position: absolute; top: calc(100% + 0.3rem); right: 0; z-index: 20; display: flex; flex-direction: column; min-width: 140px; border: 1px solid #ddd; border-radius: 8px; background: white; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12); overflow: hidden; }
.index-yml-editor-add-item { padding: 0.5rem 0.8rem; border: none; background: white; text-align: left; cursor: pointer; font-size: 0.82rem; color: #333; }
.index-yml-editor-add-item:hover:not(:disabled) { background: #eef2f9; }
.index-yml-editor-add-item:disabled { color: #aaa; cursor: not-allowed; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.index-yml-editor-graph, .index-yml-editor-code { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0.75rem; }
</style>
