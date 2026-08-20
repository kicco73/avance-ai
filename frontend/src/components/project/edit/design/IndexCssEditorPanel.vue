<script setup>
// index.css's editor pane — "Preview"/"Code" segmented toggle. Both stay mounted
// (v-show, not v-if): jumping to a definition from Preview needs CodeEditor's jumpToLine
// to work while Preview is showing, and switching to Code must never lose unsaved typing.
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

// Lives in the toolbar rather than inside ChatPreview so it stays reachable no matter
// which segment is showing — selecting a state switches to "code" (see onSelectState),
// so a pulldown tucked inside the preview segment would disappear along with it.
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

// Live unsaved buffer — ChatPreview reads straight off this, so an edit doesn't need
// to be saved to the server to preview its effect.
const content = computed(() => codeEditorRef.value?.content ?? '')
const isDirty = computed(() => codeEditorRef.value?.isDirty ?? false)
const saving = computed(() => codeEditorRef.value?.saving ?? false)

function save() { return codeEditorRef.value?.save() }
function discard() { return codeEditorRef.value?.discard() }
function undo() { return codeEditorRef.value?.undo() }
function redo() { return codeEditorRef.value?.redo() }
// Re-fetches index.css's text and can_undo/can_redo — used after a publish/revert
// invalidates undo/redo history server-side.
function reload() { return codeEditorRef.value?.reload() }

// Deliberately not the YAML-structural findStateLine/findActionLine helpers used
// elsewhere: those look for keys nested under index.yml's own blocks and would never
// match anything in a CSS buffer. This is a plain per-line substring search instead.
function findFirstLineContaining(lines, substring) {
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes(substring)) return i
  }
  return null
}

// Positions the code segment's cursor on the first `.state-<key>` occurrence without
// switching segments — the code segment stays mounted (v-show), so this just prepares
// it for whenever the user switches there. No-op if the selector isn't in the buffer.
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
