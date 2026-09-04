<script setup>
// A selected Source node's own content editor — same "Preview | Edit"
// segmented-control toolbar shape as IndexYmlEditorPanel.vue's own
// Graph/Code toggle (segments on the left, Undo/Redo always live, an
// edit-only action group — here Upload + Save — on the right), plus an
// Upload button (replaces the whole buffer with a chosen CSV file, then
// immediately saves it) since a source's own sources/<id>.csv isn't
// something a user types from scratch. Preview mirrors MdEditorPanel.vue's
// own exactly: a Markdown table rendered client-side, live off the
// current (possibly unsaved) buffer via csvMarkdownTable.js's real CSV
// parser (quoted fields, embedded commas) — never a network round-trip.
import { computed, ref } from 'vue'
import CodeEditor from '../../../CodeEditor.vue'
import { renderMarkdown } from '../../../../markdown.js'
import { csvToMarkdownTable } from '../../../../csvMarkdownTable.js'

const props = defineProps({
  projectId: { type: String, required: true },
  // sources/<id>.csv — the caller's own already-refreshed source payload
  // (see ProjectDesignPanel.vue's currentSourceArchiveName), never
  // guessed at from a bare source name.
  fileName: { type: String, required: true },
  initialSegment: { type: String, default: 'preview' }
})

const emit = defineEmits(['saved', 'renamed'])

const segment = ref(props.initialSegment)
const codeEditorRef = ref(null)
const fileInputRef = ref(null)
const uploading = ref(false)

const content = computed(() => codeEditorRef.value?.content ?? '')
const isDirty = computed(() => codeEditorRef.value?.isDirty ?? false)
const saving = computed(() => codeEditorRef.value?.saving ?? false)

const renderedHtml = computed(() => renderMarkdown(csvToMarkdownTable(content.value)))

function save() { return codeEditorRef.value?.save() }
function discard() { return codeEditorRef.value?.discard() }
function undo() { return codeEditorRef.value?.undo() }
function redo() { return codeEditorRef.value?.redo() }
function reload() { return codeEditorRef.value?.reload() }

function triggerUpload() {
  fileInputRef.value?.click()
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // reset so re-selecting the same file re-fires change
  if (!file) return
  uploading.value = true
  try {
    const text = await file.text()
    codeEditorRef.value?.setContent(text)
    await save()
  } finally {
    uploading.value = false
  }
}

defineExpose({ content, isDirty, saving, save, discard, undo, redo, reload })
</script>

<template>
  <div class="source-content-panel">
    <div class="source-content-toolbar">
      <div class="source-content-toolbar-left">
        <div class="source-content-segments">
          <button
            class="source-content-segment-btn"
            :class="{ 'source-content-segment-btn-active': segment === 'preview' }"
            @click="segment = 'preview'"
          >Preview</button>
          <button
            class="source-content-segment-btn"
            :class="{ 'source-content-segment-btn-active': segment === 'edit' }"
            @click="segment = 'edit'"
          >Edit</button>
        </div>
      </div>
      <div class="source-content-toolbar-actions">
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
          class="source-content-upload-btn"
          :disabled="uploading || codeEditorRef?.saving"
          title="Upload a CSV file, replacing this source's current content"
          @click="triggerUpload"
        >{{ uploading ? 'Uploading…' : 'Upload' }}</button>
        <input ref="fileInputRef" type="file" accept=".csv" class="source-content-upload-input" @change="handleUpload" />
        <template v-if="segment === 'edit'">
          <button
            class="save-btn"
            :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.isDirty"
            @click="codeEditorRef?.save()"
          >{{ codeEditorRef?.saving ? 'Saving…' : 'Save' }}</button>
        </template>
      </div>
    </div>

    <div v-show="segment === 'preview'" class="source-content-preview" v-html="renderedHtml"></div>

    <div v-show="segment === 'edit'" class="source-content-edit">
      <CodeEditor
        ref="codeEditorRef"
        :project-id="projectId"
        :file-name="fileName"
        @saved="emit('saved', $event)"
        @renamed="emit('renamed', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.source-content-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.source-content-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.source-content-toolbar-left { display: flex; align-items: center; gap: 0.5rem; }
.source-content-segments { display: flex; gap: 0.2rem; padding: 0.2rem; border-radius: 8px; background: #eef1f5; }
.source-content-segment-btn { padding: 0.3rem 0.8rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.82rem; color: #555; }
.source-content-segment-btn-active { background: white; color: #2c4d7a; font-weight: 600; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12); }
.source-content-upload-btn { padding: 0.35rem 0.7rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.82rem; }
.source-content-upload-btn:hover:not(:disabled) { background: #eef2f9; }
.source-content-upload-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-upload-input { display: none; }
.source-content-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-preview, .source-content-edit { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.source-content-preview { padding: 0.75rem 1rem; overflow: auto; line-height: 1.5; }
.source-content-preview :deep(table) { border-collapse: collapse; width: max-content; max-width: 100%; }
.source-content-preview :deep(th), .source-content-preview :deep(td) { border: 1px solid #ddd; padding: 0.35rem 0.6rem; font-size: 0.85rem; text-align: left; }
.source-content-preview :deep(th) { background: #f5f5f7; font-weight: 600; }
</style>
