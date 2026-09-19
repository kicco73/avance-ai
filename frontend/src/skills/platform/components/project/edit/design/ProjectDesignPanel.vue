<script setup>
import { computed, ref } from 'vue'
import FileExplorer from './FileExplorer.vue'
import CodeEditor from '../../../../CodeEditor.vue'
import IndexYmlEditorPanel from './IndexYmlEditorPanel.vue'
import IndexCssEditorPanel from './IndexCssEditorPanel.vue'
import MarkdownEditor from '../../../../MarkdownEditor.vue'
import SourceContentPanel from './SourceContentPanel.vue'
import { projectFileContentUrl } from '../../../../api.js'
import { projectFileTypes } from '../../../../../../projectFileTypes.js'
import { sourceDriverOf, WEBSEARCH_DRIVER } from '../../../../sourceDrivers.js'
import AspectMediaPanel from './AspectMediaPanel.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  files: { type: Array, default: () => [] },
  filesLoading: { type: Boolean, default: true },
  currentFileName: { type: String, default: null },
  uploading: { type: Boolean, default: false },
  creatingFile: { type: Boolean, default: false },
  explorerWidth: { type: Number, required: true },
  currentFileIsMedia: { type: Boolean, default: false },
  currentFileIsMarkdown: { type: Boolean, default: false },
  highlightedStateKey: { type: String, default: null },
  firedActionEdge: { type: Object, default: null },
  selectedElement: { type: Object, default: null },
  sources: { type: Array, default: () => [] },
  sourcesLoading: { type: Boolean, default: true },
  currentSourceName: { type: String, default: null },
  sourcesRootSelected: { type: Boolean, default: false },
  modifiedFiles: { type: Array, default: () => [] },
  currentRevision: { type: Number, default: null },
  mediaRootSelected: { type: Boolean, default: false },
  attachmentsRootSelected: { type: Boolean, default: false }
})

const currentSourceArchiveName = computed(() => (
  props.currentSourceName ? `sources/${props.currentSourceName}.csv` : null
))

const currentSourceIsWebSearch = computed(() => (
  sourceDriverOf(props.sources.find((entry) => entry.source.name === props.currentSourceName)?.source) === WEBSEARCH_DRIVER
))

const noSourceSelection = computed(() => !props.currentSourceName && !props.sourcesRootSelected)
const noRootSelection = computed(() => (
  noSourceSelection.value && !props.mediaRootSelected && !props.attachmentsRootSelected
))
const currentFileIsMediaFolder = computed(() => projectFileTypes.value.isMediaFile(props.currentFileName ?? ''))

const emit = defineEmits([
  'start-explorer-drag', 'new-attachment', 'new-aspect', 'new-legal', 'new-source', 'new-websearch-source',
  'select-file', 'select-source', 'select-sources-root', 'select-attachments-root',
  'select-media-root', 'upload-media', 'upload-attachment',
  'jump-to-definition', 'select', 'saved', 'renamed'
])

function basename(name) {
  const idx = name.lastIndexOf('/')
  return idx === -1 ? name : name.slice(idx + 1)
}

const mediaUploadInputRef = ref(null)
function triggerMediaUpload() {
  mediaUploadInputRef.value?.click()
}

const attachmentUploadInputRef = ref(null)
function triggerAttachmentUpload() {
  attachmentUploadInputRef.value?.click()
}

function downloadCurrentMedia() {
  const link = document.createElement('a')
  link.href = projectFileContentUrl(props.projectId, props.currentFileName)
  link.download = basename(props.currentFileName)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

const attachmentFiles = computed(() =>
  props.files.filter(
    (name) => name !== 'index.yml' && name !== 'index.css' && name !== 'legal/terms.md'
      && !projectFileTypes.value.isMediaFile(name)
  )
)

const codeEditorRef = ref(null)
const indexYmlEditorRef = ref(null)
const indexCssEditorRef = ref(null)
const mdEditorRef = ref(null)
const sourceContentPanelRef = ref(null)

defineExpose({ codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef, sourceContentPanelRef })
</script>

<template>
  <div class="project-design-panel">
    <FileExplorer
      :files="files"
      :files-loading="filesLoading"
      :current-file-name="currentFileName"
      :creating-file="creatingFile"
      :explorer-width="explorerWidth"
      :sources="sources"
      :sources-loading="sourcesLoading"
      :current-source-name="currentSourceName"
      :sources-root-selected="sourcesRootSelected"
      :modified-files="modifiedFiles"
      :media-root-selected="mediaRootSelected"
      :attachments-root-selected="attachmentsRootSelected"
      @new-aspect="emit('new-aspect')"
      @new-legal="emit('new-legal')"
      @select-file="emit('select-file', $event)"
      @select-source="emit('select-source', $event)"
      @select-sources-root="emit('select-sources-root')"
      @select-attachments-root="emit('select-attachments-root')"
      @select-media-root="emit('select-media-root')"
    />

    <div class="split-divider" @mousedown="emit('start-explorer-drag', $event)"></div>

    <div class="edit-project-editor-pane">
      <div v-if="sourcesRootSelected" class="edit-project-source-empty-state">
        <span class="edit-project-source-empty-icon">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
        </span>
        <p>Select a source, or add one.</p>
        <div class="edit-project-root-actions">
          <button class="media-action-btn" @click="emit('new-source')">New source</button>
          <button class="media-action-btn" @click="emit('new-websearch-source')">New web search</button>
        </div>
      </div>
      <div v-else-if="noSourceSelection && attachmentsRootSelected" class="edit-project-source-empty-state">
        <span class="edit-project-source-empty-icon">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
        </span>
        <p>Select an attachment, or add one.</p>
        <div class="edit-project-root-actions">
          <button class="media-action-btn" @click="emit('new-attachment')">New attachment</button>
          <button class="media-action-btn" @click="triggerAttachmentUpload">Upload</button>
          <input
            ref="attachmentUploadInputRef"
            type="file"
            multiple
            accept=".md,.txt"
            class="media-upload-input"
            @change="emit('upload-attachment', $event)"
          />
        </div>
      </div>
      <div v-else-if="noSourceSelection && mediaRootSelected" class="edit-project-source-empty-state">
        <span class="edit-project-source-empty-icon">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M4 4h6v6H4V4zm10 0h6v6h-6V4zM4 14h6v6H4v-6zm10 0h6v6h-6v-6z"/></svg>
        </span>
        <p>Select a media file, or upload one.</p>
        <div class="edit-project-root-actions">
          <button class="media-action-btn" :disabled="uploading" @click="triggerMediaUpload">{{ uploading ? 'Uploading…' : 'Upload' }}</button>
          <input ref="mediaUploadInputRef" type="file" multiple :accept="projectFileTypes.mediaUploadAccept" class="media-upload-input" @change="emit('upload-media', $event)" />
        </div>
      </div>
      <div v-if="currentSourceName && currentSourceIsWebSearch" class="edit-project-source-empty-state">
        <span class="edit-project-source-empty-icon">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
            <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
          </svg>
        </span>
        <p><code>source.{{ currentSourceName }}</code> reads what <code>task.websearch(…)</code> last found for the running session — nothing to edit here. Open the Websearch tab in Run &gt; Inspector to see it.</p>
      </div>
      <SourceContentPanel
        v-else-if="currentSourceName"
        :key="currentSourceName"
        ref="sourceContentPanelRef"
        :project-id="projectId"
        :file-name="currentSourceArchiveName"
        :source-name="currentSourceName"
        @saved="emit('saved', $event)"
      />
      <IndexYmlEditorPanel
        v-show="noRootSelection && currentFileName === 'index.yml'"
        ref="indexYmlEditorRef"
        :project-id="projectId"
        :attachment-files="attachmentFiles"
        :highlighted-state-key="highlightedStateKey"
        :auto-jump-on-highlight-change="true"
        :fired-action-edge="firedActionEdge"
        :selected-element="selectedElement"
        :current-revision="currentRevision"
        @jump-to-definition="emit('jump-to-definition', $event)"
        @select="emit('select', $event)"
        @saved="emit('saved', $event)"
      />
      <IndexCssEditorPanel
        v-show="noRootSelection && currentFileName === 'index.css'"
        ref="indexCssEditorRef"
        :project-id="projectId"
        :files="files"
        @saved="emit('saved', $event)"
      />
      <div
        v-if="noRootSelection && currentFileIsMedia && currentFileIsMediaFolder"
        class="edit-project-media-viewer"
      >
        <div class="edit-project-editor-toolbar">
          <span class="edit-project-editor-filename">{{ currentFileName }}</span>
          <div class="edit-project-editor-toolbar-actions">
            <button class="media-action-btn" :disabled="uploading" title="Upload media" @click="triggerMediaUpload">{{ uploading ? 'Uploading…' : 'Upload' }}</button>
            <button class="media-action-btn" title="Download this media file" @click="downloadCurrentMedia">Download</button>
            <input ref="mediaUploadInputRef" type="file" multiple :accept="projectFileTypes.mediaUploadAccept" class="media-upload-input" @change="emit('upload-media', $event)" />
          </div>
        </div>
        <AspectMediaPanel
          :key="currentFileName"
          :file-name="currentFileName"
          :content-url="projectFileContentUrl(projectId, currentFileName)"
        />
      </div>
      <AspectMediaPanel
        v-else-if="noRootSelection && currentFileIsMedia"
        :key="currentFileName"
        :file-name="currentFileName"
        :content-url="projectFileContentUrl(projectId, currentFileName)"
      />
      <MarkdownEditor
        v-else-if="noRootSelection && currentFileIsMarkdown"
        :key="currentFileName"
        ref="mdEditorRef"
        :project-id="projectId"
        :file-name="currentFileName"
        @saved="emit('saved', $event)"
        @renamed="emit('renamed', $event)"
      />
      <div
        v-else-if="noRootSelection && currentFileName !== 'index.yml' && currentFileName !== 'index.css'"
        class="edit-project-editor-attachment"
      >
        <div class="edit-project-editor-toolbar">
          <span class="edit-project-editor-filename">{{ currentFileName }}</span>
          <div class="edit-project-editor-toolbar-actions">
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
          </div>
        </div>
        <div class="edit-project-editor-content">
          <CodeEditor
            :key="currentFileName"
            ref="codeEditorRef"
            :project-id="projectId"
            :file-name="currentFileName"
            @saved="emit('saved', $event)"
            @renamed="emit('renamed', $event)"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.project-design-panel { flex: 1; display: flex; min-height: 0; }

.split-divider { flex-shrink: 0; width: 6px; margin: 0 0.4rem; border-radius: 3px; background: transparent; cursor: col-resize; }
.split-divider:hover { background: #dbe4f0; }

.edit-project-editor-pane { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
.edit-project-editor-attachment { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.edit-project-editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; background: #f5f5f7; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.edit-project-editor-filename { min-width: 0; font-size: 0.85rem; font-weight: 600; color: #333; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.edit-project-editor-toolbar-actions { display: flex; align-items: center; gap: 0.4rem; flex-shrink: 0; }
.undo-redo-btn { width: 1.8rem; height: 1.8rem; line-height: 1; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 1rem; }
.undo-redo-btn:hover:not(:disabled) { background: #eef2f9; }
.undo-redo-btn:disabled { border-color: #ccc; color: #ccc; cursor: not-allowed; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.edit-project-editor-content { flex: 1; min-height: 0; display: flex; }
.edit-project-source-empty-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.4rem; padding: 1rem; text-align: center; color: #777; }
.edit-project-source-empty-icon { color: #3949ab; opacity: 0.6; }
.edit-project-source-empty-state p { margin: 0; font-size: 0.9rem; }
.edit-project-root-actions { display: flex; gap: 0.5rem; margin-top: 0.3rem; }
.edit-project-media-viewer { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.media-action-btn { padding: 0.35rem 0.7rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.82rem; }
.media-action-btn:hover:not(:disabled) { background: #eef2f9; }
.media-action-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.media-upload-input { display: none; }
</style>
