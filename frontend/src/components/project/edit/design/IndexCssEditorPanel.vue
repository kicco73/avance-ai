<script setup>
// index.css's own dedicated editor pane — a "Preview"/"Code" segmented
// toggle, same pattern as IndexYmlEditorPanel.vue's own "Graph"/"Code"
// segments: CodeEditor (the same one every other project file uses, here
// pinned to index.css) for the code segment, ChatPreview.vue (static
// mock messages, never real chat data) for the preview one. Both segments
// stay mounted at once (v-show, not v-if) — same reason IndexYmlEditorPanel
// keeps its own two mounted: ChatPreview's own state pulldown needs
// CodeEditor's own jumpToLine to work even while "preview" is the one
// actually showing, and switching back to "code" must never lose
// whatever's been typed.
import { computed, onMounted, ref } from 'vue'
import CodeEditor from '../../../CodeEditor.vue'
import ChatPreview from './ChatPreview.vue'
import { getProjectGraph } from '../../../../api.js'

const props = defineProps({
  projectName: { type: String, required: true }
})

const emit = defineEmits(['saved'])

const segment = ref('preview')
const codeEditorRef = ref(null)
const previewRef = ref(null)

// The preview's own state pulldown — lives in this view's toolbar (not
// inside ChatPreview.vue itself) so it stays reachable regardless of
// which segment is actually showing: jumping to a definition switches to
// "code" (see onSelectState below), and the previous location (tucked
// inside the "preview" segment's own body) would then disappear along
// with it, making a second jump impossible without switching back first.
const stateNodes = ref([])
const selectedStateKey = ref('')

async function loadStateNodes() {
  try {
    const { nodes } = await getProjectGraph(props.projectName)
    stateNodes.value = nodes.map((n) => n.state)
  } catch {
    stateNodes.value = []
  }
}

onMounted(loadStateNodes)

// The live, unsaved buffer — ChatPreview's own CSS prop reads straight off
// this, no round-trip to the server needed to see the effect of an edit
// that hasn't been saved yet (see CodeEditor.vue's own `content` ref,
// updated on every keystroke via its updateListener).
const content = computed(() => codeEditorRef.value?.content ?? '')
const isDirty = computed(() => codeEditorRef.value?.isDirty ?? false)
const saving = computed(() => codeEditorRef.value?.saving ?? false)

function save() { return codeEditorRef.value?.save() }
function discard() { return codeEditorRef.value?.discard() }
function undo() { return codeEditorRef.value?.undo() }
function redo() { return codeEditorRef.value?.redo() }
// Re-fetches index.css's own text and can_undo/can_redo — for after a
// publish/revert just invalidated its undo/redo history server-side (see
// EditProjectView.vue's own refreshActiveEditorHistory), the same reason
// IndexYmlEditorPanel.vue's own reloadCode exists.
function reload() { return codeEditorRef.value?.reload() }

// Best-effort line lookup for the first textual occurrence of a CSS
// selector — deliberately not editor_design's own findStateLine/
// findActionLine/findSignalLine (EditProjectView.vue): those scan for a
// direct child key nested under index.yml's own top-level `states:`/
// `signals:` block, a YAML-structural search with no notion of CSS syntax
// at all — reused verbatim they would simply never match anything in a
// CSS buffer. This is the CSS-appropriate equivalent: a plain per-line
// substring search.
function findFirstLineContaining(lines, substring) {
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes(substring)) return i
  }
  return null
}

// Selecting a state from the toolbar pulldown: ChatPreview picks up the
// new state-<key> class itself (reactive off selectedStateKey, passed
// down as a prop), and this also positions the code segment's own cursor
// on the first occurrence of `.state-<key>` — never switches the
// segmented control itself (the code segment stays mounted, v-show, even
// while "preview" is the one actually showing, so this is purely
// preparing it for whenever the user switches there themselves). No
// scroll, no error when that selector doesn't exist in the buffer at all.
function onSelectState() {
  const stateKey = selectedStateKey.value
  if (!stateKey) return
  const lines = content.value.split('\n')
  const lineIndex = findFirstLineContaining(lines, `.state-${stateKey}`)
  if (lineIndex === null) return
  codeEditorRef.value?.jumpToLine(lineIndex)
}

defineExpose({ content, isDirty, saving, save, discard, undo, redo, reload })
</script>

<template>
  <div class="index-css-editor">
    <div class="index-css-editor-toolbar">
      <div class="index-css-editor-segments">
        <button
          class="index-css-editor-segment-btn"
          :class="{ 'index-css-editor-segment-btn-active': segment === 'preview' }"
          @click="segment = 'preview'"
        >Preview</button>
        <button
          class="index-css-editor-segment-btn"
          :class="{ 'index-css-editor-segment-btn-active': segment === 'code' }"
          @click="segment = 'code'"
        >Code</button>
      </div>
      <div class="index-css-editor-toolbar-actions">
        <select v-model="selectedStateKey" class="chat-preview-state-select" @change="onSelectState">
          <option value="">— Preview state —</option>
          <option v-for="node in stateNodes" :key="node.key" :value="node.key">{{ node.ui_label }}</option>
        </select>
        <template v-if="segment === 'code'">
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
          <button
            class="save-btn"
            :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.isDirty"
            @click="codeEditorRef?.save()"
          >{{ codeEditorRef?.saving ? 'Saving…' : 'Save' }}</button>
        </template>
      </div>
    </div>

    <div v-show="segment === 'preview'" class="index-css-editor-preview">
      <ChatPreview ref="previewRef" :css="content" :state-key="selectedStateKey" />
    </div>

    <div v-show="segment === 'code'" class="index-css-editor-code">
      <CodeEditor
        ref="codeEditorRef"
        :project-name="projectName"
        file-name="index.css"
        @saved="emit('saved', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.index-css-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.index-css-editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.index-css-editor-segments { display: flex; gap: 0.2rem; padding: 0.2rem; border-radius: 8px; background: #eef1f5; }
.index-css-editor-segment-btn { padding: 0.3rem 0.8rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.82rem; color: #555; }
.index-css-editor-segment-btn-active { background: white; color: #2c4d7a; font-weight: 600; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12); }
.index-css-editor-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; }
.chat-preview-state-select { padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; font-size: 0.82rem; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.index-css-editor-preview, .index-css-editor-code { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0.75rem; }
</style>
