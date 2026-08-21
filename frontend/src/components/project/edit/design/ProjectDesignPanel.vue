<script setup>
// Design mode's file explorer plus whichever editor fits the current file:
// index.yml/index.css get dedicated panels, images get a preview, a
// .txt/.md attachment gets MdEditorPanel's Preview/Edit toggle, anything
// else falls back to a bare CodeEditor. Purely presentational — state is
// owned by EditProjectView.vue and reached only through props/emits.
import { computed, ref } from 'vue'
import FileExplorer from './FileExplorer.vue'
import CodeEditor from '../../../CodeEditor.vue'
import IndexYmlEditorPanel from './IndexYmlEditorPanel.vue'
import IndexCssEditorPanel from './IndexCssEditorPanel.vue'
import MdEditorPanel from './MdEditorPanel.vue'
import { projectFileContentUrl } from '../../../../api.js'

const IMAGE_PATTERN = /\.(png|jpe?g|gif|webp|svg)$/i

const props = defineProps({
  projectName: { type: String, required: true },
  files: { type: Array, default: () => [] },
  filesLoading: { type: Boolean, default: true },
  currentFileName: { type: String, default: null },
  uploading: { type: Boolean, default: false },
  creatingFile: { type: Boolean, default: false },
  explorerWidth: { type: Number, required: true },
  // Gates mounting IndexYmlEditorPanel/CodeEditor: each loads its content
  // as soon as it mounts, so without this they could race the project's
  // undo/redo history being cleared on entry.
  historyCleared: { type: Boolean, default: false },
  currentFileIsImage: { type: Boolean, default: false },
  // A .txt/.md attachment — gets MdEditorPanel instead of the bare
  // CodeEditor fallback below.
  currentFileIsMarkdown: { type: Boolean, default: false },
  highlightedStateKey: { type: String, default: null },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  selectedElement: { type: Object, default: null }
})

const emit = defineEmits([
  'start-explorer-drag', 'new-file', 'select-file', 'upload-file',
  'jump-to-definition', 'select', 'saved'
])

// The Behavior branch's own attachments (see FileExplorer.vue's identical
// grouping) — index.yml's code segment offers these for `attachments:` autocomplete.
const attachmentFiles = computed(() =>
  props.files.filter((name) => name !== 'index.yml' && name !== 'index.css' && !IMAGE_PATTERN.test(name))
)

const codeEditorRef = ref(null)
const indexYmlEditorRef = ref(null)
const indexCssEditorRef = ref(null)
const mdEditorRef = ref(null)

// Exposed so EditProjectView.vue can reach the editor instances directly for
// things a prop/emit can't express (jumpToLine, save/discard/undo/redo, reload, mediaType, ...).
defineExpose({ codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef })
</script>

<template>
  <div class="project-design-panel">
    <FileExplorer
      :files="files"
      :files-loading="filesLoading"
      :current-file-name="currentFileName"
      :uploading="uploading"
      :creating-file="creatingFile"
      :explorer-width="explorerWidth"
      @new-file="emit('new-file')"
      @select-file="emit('select-file', $event)"
      @upload-file="emit('upload-file', $event)"
    />

    <div class="split-divider" @mousedown="emit('start-explorer-drag', $event)"></div>

    <div class="edit-project-editor-pane">
      <p v-if="!historyCleared" class="edit-project-status">Loading…</p>
      <template v-else>
        <!-- Stays mounted (v-show, not v-if) even while a different file is
             open: its InspectorGraph is the only place the Inspector's
             "State"/"Actions" selection is resolved from, so unmounting it
             would drop a still-valid selection whenever an attachment is viewed. -->
        <IndexYmlEditorPanel
          v-show="currentFileName === 'index.yml'"
          ref="indexYmlEditorRef"
          :project-name="projectName"
          :attachment-files="attachmentFiles"
          :highlighted-state-key="highlightedStateKey"
          :auto-jump-on-highlight-change="true"
          :next-action-edge="nextActionEdge"
          :fired-action-edge="firedActionEdge"
          :selected-element="selectedElement"
          @jump-to-definition="emit('jump-to-definition', $event)"
          @select="emit('select', $event)"
          @saved="emit('saved', $event)"
        />
        <IndexCssEditorPanel
          v-show="currentFileName === 'index.css'"
          ref="indexCssEditorRef"
          :project-name="projectName"
          :files="files"
          @saved="emit('saved', $event)"
        />
        <div v-if="currentFileIsImage" class="edit-project-editor-attachment">
          <div class="edit-project-editor-content edit-project-editor-image">
            <img :key="currentFileName" :src="projectFileContentUrl(projectName, currentFileName)" :alt="currentFileName" />
          </div>
        </div>
        <MdEditorPanel
          v-else-if="currentFileIsMarkdown"
          :key="currentFileName"
          ref="mdEditorRef"
          :project-name="projectName"
          :file-name="currentFileName"
          @saved="emit('saved', $event)"
        />
        <div v-else-if="currentFileName !== 'index.yml' && currentFileName !== 'index.css'" class="edit-project-editor-attachment">
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
              :project-name="projectName"
              :file-name="currentFileName"
              @saved="emit('saved', $event)"
            />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.project-design-panel { flex: 1; display: flex; min-height: 0; }

.split-divider { flex-shrink: 0; width: 6px; margin: 0 0.4rem; border-radius: 3px; background: transparent; cursor: col-resize; }
.split-divider:hover { background: #dbe4f0; }

.edit-project-editor-pane { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
/* Same shape as IndexYmlEditorPanel's root .index-yml-editor — the two
   fill this pane, one hidden via v-show while the other's showing. */
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
.edit-project-editor-image { align-items: center; justify-content: center; overflow: auto; background: repeating-conic-gradient(#f0f0f0 0% 25%, #fafafa 0% 50%) 50% / 20px 20px; }
.edit-project-editor-image img { max-width: 100%; max-height: 100%; object-fit: contain; }
.edit-project-status { margin: auto; color: #444; }
</style>
