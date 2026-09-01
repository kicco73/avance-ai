<script setup>
// A .txt/.md attachment's editor pane — "Preview"/"Edit" segmented toggle,
// same shape as IndexCssEditorPanel.vue. Preview renders the live buffer
// through the same renderMarkdown() the chat bubbles use; Edit is a plain
// CodeEditor (its own markdown() language mode kicks in off content_type —
// see CodeEditor.vue). Remounted per file (:key="fileName" in
// ProjectDesignPanel.vue), unlike index.yml/index.css's own fixed-name panels.
import { computed, ref } from 'vue'
import CodeEditor from '../../../CodeEditor.vue'
import DocInfoButton from '../../../DocInfoButton.vue'
import { renderMarkdown } from '../../../../markdown.js'

const props = defineProps({
  projectName: { type: String, required: true },
  fileName: { type: String, required: true },
  // 'edit' for a file a create/upload/new-legal flow just opened (see
  // ProjectDesignPanel.vue) — read once at mount, same as fileName
  // itself: this component remounts fresh per file (:key="fileName"),
  // so there's no later file switch for a reactive prop to catch.
  initialSegment: { type: String, default: 'preview' }
})

const emit = defineEmits(['saved'])

const segment = ref(props.initialSegment)
const codeEditorRef = ref(null)

const content = computed(() => codeEditorRef.value?.content ?? '')
const isDirty = computed(() => codeEditorRef.value?.isDirty ?? false)
const saving = computed(() => codeEditorRef.value?.saving ?? false)
const renderedHtml = computed(() => renderMarkdown(content.value))

function save() { return codeEditorRef.value?.save() }
function discard() { return codeEditorRef.value?.discard() }
function undo() { return codeEditorRef.value?.undo() }
function redo() { return codeEditorRef.value?.redo() }
function reload() { return codeEditorRef.value?.reload() }

defineExpose({ content, isDirty, saving, save, discard, undo, redo, reload })
</script>

<template>
  <div class="md-editor">
    <div class="md-editor-toolbar">
      <div class="md-editor-segments">
        <button
          class="md-editor-segment-btn"
          :class="{ 'md-editor-segment-btn-active': segment === 'preview' }"
          @click="segment = 'preview'"
        >Preview</button>
        <button
          class="md-editor-segment-btn"
          :class="{ 'md-editor-segment-btn-active': segment === 'edit' }"
          @click="segment = 'edit'"
        >Edit</button>
      </div>
      <div v-if="segment === 'edit'" class="md-editor-toolbar-actions">
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
        <DocInfoButton doc-name="markdown-guide" title="Markdown syntax" />
      </div>
    </div>

    <div v-show="segment === 'preview'" class="md-editor-preview" v-html="renderedHtml"></div>

    <div v-show="segment === 'edit'" class="md-editor-edit">
      <CodeEditor
        ref="codeEditorRef"
        :project-name="projectName"
        :file-name="fileName"
        @saved="emit('saved', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.md-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.md-editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.md-editor-segments { display: flex; gap: 0.2rem; padding: 0.2rem; border-radius: 8px; background: #eef1f5; flex-shrink: 0; }
.md-editor-segment-btn { padding: 0.3rem 0.8rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.82rem; color: #555; }
.md-editor-segment-btn-active { background: white; color: #2c4d7a; font-weight: 600; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12); }
.md-editor-toolbar-actions { display: flex; align-items: center; gap: 0.4rem; flex-shrink: 0; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.md-editor-preview, .md-editor-edit { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.md-editor-preview { padding: 0.75rem 1rem; overflow-y: auto; line-height: 1.5; }
.md-editor-preview :deep(pre) { background: #f5f5f7; border-radius: 6px; padding: 0.6rem 0.75rem; overflow-x: auto; }
.md-editor-preview :deep(code) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.85em; }
.md-editor-preview :deep(img) { max-width: 100%; }
.md-editor-preview :deep(blockquote) { margin: 0; padding-left: 0.75rem; border-left: 3px solid #ddd; color: #555; }
</style>
