<script setup>
// index.css's editor pane — preview and code editor shown side by side, split
// by a draggable divider (same useResizablePanel composable as
// EditProjectView.vue's own file-explorer split).
import { computed, onMounted, ref } from 'vue'
import CodeEditor from '../../../CodeEditor.vue'
import ChatPreview from './ChatPreview.vue'
import DocInfoButton from '../../../DocInfoButton.vue'
import { aiEditIndexCss, getProjectGraph } from '../../../../api.js'
import { invalidateSkin } from '../../../../chatStore.js'
import { textareaDialog } from '../../../../dialogStore.js'
import { useResizablePanel } from '../../../../composables/useResizablePanel.js'

const props = defineProps({
  projectName: { type: String, required: true },
  files: { type: Array, default: () => [] }
})

const emit = defineEmits(['saved'])

// The Theme branch's own image-extension test (see FileExplorer.vue/
// EditProjectView.vue) — the basenames the code editor's url(...)
// autocomplete offers.
const IMAGE_PATTERN = /\.(png|jpe?g|gif|webp|svg)$/i
const cssAssetFiles = computed(() => props.files.filter((name) => IMAGE_PATTERN.test(name)))

const codeEditorRef = ref(null)
const previewRef = ref(null)
const aiEditing = ref(false)
const { width: previewWidth, startDrag: startPreviewDrag } = useResizablePanel(420, { min: 280, max: 640 })

// Lives in the toolbar rather than inside ChatPreview since it drives both
// panels at once — the preview's own `.state-<key>` class (via ChatPreview's
// state-key prop) and the code editor's cursor jump (see onSelectState).
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

// The toolbar's AI button: prompts for a free-form problem/change
// description, sends it (with the project's current index.css, its own
// uploaded aspect/ asset names, and the skin format spec) to the
// backend's AiService, and drops the rewritten content straight into the
// code buffer — left dirty, ready for Save, same as any manual edit.
async function aiEdit() {
  const instruction = await textareaDialog({
    title: 'AI-assisted edit',
    body: 'Describe the problem to solve or the change to make in index.css.',
    placeholder: 'e.g. Give the header a dark background and round the header icon…'
  })
  if (!instruction) return
  aiEditing.value = true
  try {
    const result = await aiEditIndexCss(props.projectName, instruction)
    codeEditorRef.value?.setContent(result.content)
  } catch {
    // already surfaced via apiFetch's shared error store
  } finally {
    aiEditing.value = false
  }
}

// Deliberately not the YAML-structural findStateLine/findActionLine helpers used
// elsewhere: those look for keys nested under index.yml's own blocks and would never
// match anything in a CSS buffer. This is a plain per-line substring search instead.
function findFirstLineContaining(lines, substring) {
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes(substring)) return i
  }
  return null
}

// Positions the code editor's cursor on the first `.state-<key>` occurrence — the
// editor is always visible alongside the preview, so this jumps straight there.
// No-op if the selector isn't in the buffer.
function onSelectState() {
  const stateKey = selectedStateKey.value
  if (!stateKey) return
  const lines = content.value.split('\n')
  const lineIndex = findFirstLineContaining(lines, `.state-${stateKey}`)
  if (lineIndex === null) return
  codeEditorRef.value?.jumpToLine(lineIndex)
}

// The live Test chat (a real ChatWindow, unlike ChatPreview above which
// already reflects `content` live) fetches its skin over HTTP and only
// re-fetches on a project/session change — a save alone wouldn't
// otherwise reach it, so this is what tells it the file actually changed.
function onCodeSaved(result) {
  invalidateSkin()
  emit('saved', result)
}

defineExpose({ content, isDirty, saving, save, discard, undo, redo, reload })
</script>

<template>
  <div class="index-css-editor">
    <div class="index-css-editor-toolbar">
      <div class="index-css-editor-toolbar-left">
        <select v-model="selectedStateKey" class="chat-preview-state-select" @change="onSelectState">
          <option value="">— Preview state —</option>
          <option v-for="node in stateNodes" :key="node.key" :value="node.key">{{ node.ui_label }}</option>
        </select>
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
      <div class="index-css-editor-toolbar-actions">
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
        <DocInfoButton doc-name="skin-specs" title="Chat skin" />
      </div>
    </div>

    <div class="index-css-editor-split">
      <div class="index-css-editor-preview" :style="{ width: previewWidth + 'px' }">
        <ChatPreview ref="previewRef" :css="content" :state-key="selectedStateKey" :project-name="projectName" />
      </div>

      <div class="index-css-editor-split-divider" @mousedown="startPreviewDrag"></div>

      <div class="index-css-editor-code">
        <CodeEditor
          ref="codeEditorRef"
          :project-name="projectName"
          file-name="index.css"
          :css-asset-files="cssAssetFiles"
          @saved="onCodeSaved"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.index-css-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.index-css-editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.index-css-editor-toolbar-left { display: flex; align-items: center; gap: 0.5rem; }
.index-css-editor-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; }
.chat-preview-state-select { padding: 0.35rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; font-size: 0.82rem; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ai-edit-btn { display: flex; align-items: center; justify-content: center; width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 6px; border: 1px solid #ccc; background: white; color: #8b5cf6; cursor: pointer; }
.ai-edit-btn:hover:not(:disabled) { background: #f5f0fe; border-color: #8b5cf6; }
.ai-edit-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ai-edit-btn-loading svg { animation: ai-edit-btn-spin 1.1s linear infinite; }
@keyframes ai-edit-btn-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }

.index-css-editor-split { flex: 1; display: flex; min-height: 0; }
.index-css-editor-preview { flex-shrink: 0; min-height: 0; display: flex; flex-direction: column; padding: 0.75rem; }
.index-css-editor-split-divider { flex-shrink: 0; width: 6px; margin: 0 0.4rem; border-radius: 3px; background: transparent; cursor: col-resize; }
.index-css-editor-split-divider:hover { background: #dbe4f0; }
.index-css-editor-code { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 0.75rem; }
</style>
