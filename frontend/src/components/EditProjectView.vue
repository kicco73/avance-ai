<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { getProjectFiles, getProjectFile, putProjectFile, deleteProjectFile, getProjectSignals } from '../api.js'
import { clearApiError, errorDetail, errorMessage, setApiError } from '../errorStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close', 'saved'])

const UPLOADABLE_PATTERN = /\.(txt|ya?ml)$/i
const YAML_PATTERN = /\.ya?ml$/i

const filesLoading = ref(true)
const files = ref([])
const currentFileName = ref('index.yml')

const loading = ref(true)
const saving = ref(false)
const uploading = ref(false)
const creatingFile = ref(false)
const deletingFile = ref(null)
const showErrorDetail = ref(false)
const editorHost = ref(null)
const uploadInput = ref(null)

// Inspect panel: shows the last-saved project's signal definitions,
// analogous to SignalsView — see toggleInspect/loadSignals.
const inspecting = ref(false)
const signalsLoading = ref(true)
const signals = ref([])
const inspectorWidth = ref(360)

// The editor's own doc is the source of truth for content while it's
// mounted — this ref only mirrors it (via the updateListener below) so
// save() has something to send without querying the view directly.
const content = ref('')
// What was last loaded/saved for the current file — compared against
// `content` to decide whether switching/closing needs a confirmation.
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)

// Set while the unsaved-changes dialog is blocking a switch to this file —
// resolved one way or another by confirmSwitchSave/Discard/Cancel.
const pendingFileName = ref(null)

// Left panel width in px, adjusted by dragging the split divider.
const explorerWidth = ref(220)
// Which divider (if any) is currently being dragged — 'explorer' or
// 'inspector' — read by the single shared onDrag/stopDrag pair below.
let dragTarget = null

let view = null
const editableCompartment = new Compartment()

function createEditor(doc, fileName) {
  const extensions = [
    basicSetup,
    EditorView.lineWrapping,
    editableCompartment.of(EditorView.editable.of(true)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) content.value = update.state.doc.toString()
    })
  ]
  if (YAML_PATTERN.test(fileName)) extensions.splice(1, 0, yaml())
  view = new EditorView({ doc, extensions, parent: editorHost.value })
}

function destroyEditor() {
  view?.destroy()
  view = null
}

async function loadFiles() {
  filesLoading.value = true
  try {
    files.value = (await getProjectFiles(props.projectName)).files
  } catch {
    // already surfaced via apiFetch
  } finally {
    filesLoading.value = false
  }
}

async function loadFileContent(fileName) {
  loading.value = true
  clearApiError()
  destroyEditor()
  try {
    content.value = await getProjectFile(props.projectName, fileName)
    originalContent.value = content.value
  } catch {
    loading.value = false
    return
  }
  loading.value = false
  await nextTick() // editorHost is v-show, but wait a tick anyway for layout to settle
  createEditor(content.value, fileName)
}

// Saves whatever file is currently open, in place. Shared by the header's
// Save button (which also leaves the editor, see save()) and the
// switch-file dialog's "Save" choice (which doesn't). Returns whether it
// succeeded; on failure the shared error store already has the message.
async function saveCurrentFile() {
  saving.value = true
  clearApiError()
  try {
    await putProjectFile(props.projectName, currentFileName.value, content.value)
    originalContent.value = content.value
    emit('saved')
    // The Inspect panel reflects the last saved state, so a successful
    // save is exactly when it needs to catch up (see toggleInspect).
    if (inspecting.value) await loadSignals()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

async function save() {
  if (await saveCurrentFile()) emit('close')
}

async function switchFile(fileName) {
  currentFileName.value = fileName
  await loadFileContent(fileName)
}

// Entry point for both explorer clicks and post-upload auto-open — routes
// through the unsaved-changes dialog when there's something to lose.
async function selectFile(fileName) {
  if (fileName === currentFileName.value) return
  if (isDirty.value) {
    pendingFileName.value = fileName
    return
  }
  await switchFile(fileName)
}

async function confirmSwitchSave() {
  const target = pendingFileName.value
  pendingFileName.value = null
  if (await saveCurrentFile()) await switchFile(target)
}

async function confirmSwitchDiscard() {
  const target = pendingFileName.value
  pendingFileName.value = null
  await switchFile(target)
}

function confirmSwitchCancel() {
  pendingFileName.value = null
}

function triggerUpload() {
  uploadInput.value?.click()
}

async function handleUploadFile(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // reset so re-selecting the same file re-fires change
  if (!file) return
  if (!UPLOADABLE_PATTERN.test(file.name)) {
    setApiError('Only .txt or .yml/.yaml files can be uploaded.')
    return
  }
  uploading.value = true
  clearApiError()
  try {
    const text = await file.text()
    await putProjectFile(props.projectName, file.name, text)
    await loadFiles()
    await selectFile(file.name)
  } catch {
    // already surfaced via apiFetch
  } finally {
    uploading.value = false
  }
}

async function handleNewFile() {
  const rawName = window.prompt('New file name (e.g. notes.txt or extra.yml):')
  if (rawName === null) return // cancelled
  const name = rawName.trim()
  if (!name) return
  if (!UPLOADABLE_PATTERN.test(name)) {
    setApiError('Only .txt or .yml/.yaml files can be created.')
    return
  }
  if (files.value.includes(name)) {
    setApiError(`A file named "${name}" already exists.`)
    return
  }
  creatingFile.value = true
  clearApiError()
  try {
    await putProjectFile(props.projectName, name, '')
    await loadFiles()
    await selectFile(name)
  } catch {
    // already surfaced via apiFetch
  } finally {
    creatingFile.value = false
  }
}

// index.yml is protected server-side too (delete_project_file rejects it) —
// the button is also hidden for it in the template, this is just a second
// guard against a stale click.
async function handleDeleteFile(fileName) {
  if (fileName === 'index.yml') return
  if (!window.confirm(`Delete file "${fileName}"? This cannot be undone.`)) return
  deletingFile.value = fileName
  clearApiError()
  try {
    await deleteProjectFile(props.projectName, fileName)
    await loadFiles()
    if (fileName === currentFileName.value) await switchFile('index.yml')
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingFile.value = null
  }
}

// Only prompts when there's actually something to lose — a clean editor
// (nothing typed, or already saved) closes straight away.
function handleClose() {
  if (isDirty.value && !window.confirm('Discard unsaved changes to this file?')) return
  emit('close')
}

async function loadSignals() {
  signalsLoading.value = true
  try {
    signals.value = (await getProjectSignals(props.projectName)).signals
  } catch {
    // already surfaced via apiFetch
  } finally {
    signalsLoading.value = false
  }
}

// Toggled by the Inspect button and by the panel's own Close button, same
// as SignalsView's autotracking/close pair.
async function toggleInspect() {
  inspecting.value = !inspecting.value
  if (inspecting.value) await loadSignals()
}

function startExplorerDrag(event) {
  dragTarget = 'explorer'
  event.preventDefault()
}

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget === 'explorer') {
    explorerWidth.value = Math.min(420, Math.max(160, explorerWidth.value + event.movementX))
  } else if (dragTarget === 'inspector') {
    // The inspector's divider sits on its left edge, so dragging it left
    // (negative movementX) needs to grow the panel, not shrink it.
    inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
  }
}

function stopDrag() {
  dragTarget = null
}

watch(saving, (isSaving) => {
  view?.dispatch({ effects: editableCompartment.reconfigure(EditorView.editable.of(!isSaving)) })
})

onMounted(() => {
  loadFiles()
  loadFileContent(currentFileName.value)
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
})
onBeforeUnmount(() => {
  destroyEditor()
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
})
</script>

<template>
  <div class="edit-project-overlay">
    <div class="edit-project-header">
      <h2>Edit project — {{ projectName }} <span class="edit-project-current-file">/ {{ currentFileName }}</span></h2>
      <div class="edit-project-header-actions">
        <button
          class="inspect-btn"
          :class="{ 'inspect-btn-on': inspecting }"
          @click="toggleInspect"
        >
          Inspect
        </button>
        <button class="save-btn" :disabled="loading || saving" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
        <button class="close-btn" @click="handleClose">Back</button>
      </div>
    </div>

    <div v-if="errorMessage" class="edit-project-error-row">
      <p class="edit-project-error">{{ errorMessage }}</p>
      <button
        v-if="errorDetail"
        type="button"
        class="edit-project-error-details-btn"
        @click="showErrorDetail = !showErrorDetail"
      >
        {{ showErrorDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>
    <pre v-if="errorMessage && errorDetail && showErrorDetail" class="edit-project-error-detail">{{ errorDetail }}</pre>

    <div class="edit-project-body">
      <div class="file-explorer" :style="{ width: explorerWidth + 'px' }">
        <div class="file-explorer-header">
          <span class="file-explorer-title">Files</span>
          <div class="file-explorer-header-actions">
            <button class="file-explorer-new-btn" :disabled="creatingFile" @click="handleNewFile">
              {{ creatingFile ? 'Creating…' : '+ New' }}
            </button>
            <button class="file-explorer-upload-btn" :disabled="uploading" @click="triggerUpload">
              {{ uploading ? 'Uploading…' : '+ Upload' }}
            </button>
          </div>
          <input
            ref="uploadInput"
            type="file"
            accept=".txt,.yml,.yaml"
            class="file-explorer-upload-input"
            @change="handleUploadFile"
          />
        </div>
        <p v-if="filesLoading" class="file-explorer-status">Loading…</p>
        <ul v-else class="file-explorer-list">
          <li v-for="name in files" :key="name" class="file-explorer-row">
            <button
              class="file-explorer-item"
              :class="{ 'file-explorer-item-active': name === currentFileName }"
              :title="name"
              @click="selectFile(name)"
            >
              {{ name }}
            </button>
            <button
              v-if="name !== 'index.yml'"
              class="file-explorer-delete-btn"
              :disabled="deletingFile === name"
              title="Delete file"
              @click="handleDeleteFile(name)"
            >
              ×
            </button>
          </li>
        </ul>
      </div>

      <div class="split-divider" @mousedown="startExplorerDrag"></div>

      <div class="edit-project-editor-pane">
        <p v-if="loading" class="edit-project-status">Loading…</p>
        <div v-show="!loading" ref="editorHost" class="edit-project-editor"></div>
      </div>

      <template v-if="inspecting">
        <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

        <div class="inspector-panel" :style="{ '--inspector-width': inspectorWidth + 'px' }">
          <div class="inspector-header">
            <h3>Signals</h3>
            <button class="close-btn" @click="toggleInspect">Close</button>
          </div>
          <div class="inspector-body">
            <p v-if="signalsLoading" class="signals-status">Loading…</p>
            <p v-else-if="!signals.length" class="signals-status">No signals defined.</p>
            <div v-else class="inspector-signal-list">
              <div v-for="signal in signals" :key="signal.name" class="inspector-signal-block">
                <span class="inspector-signal-name">{{ signal.ui_label || signal.name }}</span>
                <span v-if="signal.description" class="inspector-signal-description">
                  {{ signal.description }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div v-if="pendingFileName" class="switch-dialog-overlay">
      <div class="switch-dialog">
        <p>"{{ currentFileName }}" has unsaved changes. Save before switching to "{{ pendingFileName }}"?</p>
        <div class="switch-dialog-actions">
          <button class="switch-dialog-save-btn" :disabled="saving" @click="confirmSwitchSave">Save</button>
          <button class="switch-dialog-discard-btn" :disabled="saving" @click="confirmSwitchDiscard">Discard</button>
          <button class="switch-dialog-cancel-btn" :disabled="saving" @click="confirmSwitchCancel">Cancel</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.edit-project-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.edit-project-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.edit-project-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.edit-project-current-file {
  font-weight: 400;
  color: #666;
  font-size: 0.95rem;
}

.edit-project-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.save-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
}

.save-btn:hover:not(:disabled) {
  background: #256428;
}

.save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
}

.inspect-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.inspect-btn:hover {
  background: #eef2f9;
}

.inspect-btn-on {
  background: #4a6fa5;
  color: white;
}

.inspect-btn-on:hover {
  background: #3d5c8a;
}

.edit-project-error-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
}

.edit-project-error {
  margin: 0;
  color: #c62828;
  font-size: 0.9rem;
  flex: 1;
}

.edit-project-error-details-btn {
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.8rem;
}

.edit-project-error-detail {
  margin: 0;
  padding: 0.75rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
  color: #7a1f1f;
  font-size: 0.8rem;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

.edit-project-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.file-explorer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.file-explorer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid #ddd;
  background: #f7f8fa;
}

.file-explorer-header-actions {
  display: flex;
  gap: 0.4rem;
}

.file-explorer-new-btn {
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.78rem;
}

.file-explorer-new-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.file-explorer-new-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.file-explorer-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.file-explorer-upload-btn {
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.78rem;
}

.file-explorer-upload-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.file-explorer-upload-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.file-explorer-upload-input {
  display: none;
}

.file-explorer-status {
  margin: 0;
  padding: 0.6rem;
  font-size: 0.85rem;
  color: #444;
}

.file-explorer-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem;
  overflow-y: auto;
  flex: 1;
}

.file-explorer-row {
  display: flex;
  align-items: center;
  gap: 0.2rem;
}

.file-explorer-item {
  flex: 1;
  min-width: 0;
  display: block;
  text-align: left;
  padding: 0.4rem 0.5rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.file-explorer-item:hover {
  background: #f0f4fa;
}

.file-explorer-item-active {
  background: #e4ecf9;
  color: #2c4d7a;
  font-weight: 600;
}

.file-explorer-delete-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
  font-size: 1rem;
}

.file-explorer-delete-btn:hover:not(:disabled) {
  background: #fdecea;
}

.file-explorer-delete-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.split-divider {
  flex-shrink: 0;
  width: 6px;
  margin: 0 0.4rem;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}

.split-divider:hover {
  background: #dbe4f0;
}

.edit-project-editor-pane {
  flex: 1;
  min-width: 0;
  display: flex;
}

.edit-project-status {
  margin: auto;
  color: #444;
}

.edit-project-editor {
  flex: 1;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.edit-project-editor :deep(.cm-editor) {
  height: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
}

.edit-project-editor :deep(.cm-scroller) {
  overflow: auto;
  line-height: 1.5;
}

.edit-project-editor :deep(.cm-editor.cm-focused) {
  outline: none;
}

/* Narrow screens: the inspector takes over the whole editor overlay, same
   as SignalsView's own narrow-screen behavior — there isn't room to dock
   it beside the editor and keep both usable. */
@media (max-width: 899.98px) {
  .inspector-divider {
    display: none;
  }
}

.inspector-panel {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 150;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

@media (min-width: 900px) {
  .inspector-panel {
    /* Wide screens: docked beside the editor, both visible at once —
       width comes from the drag-adjusted --inspector-width variable. */
    position: static;
    inset: auto;
    z-index: auto;
    flex-shrink: 0;
    width: var(--inspector-width);
    border: 1px solid #ddd;
    border-radius: 8px;
    overflow: hidden;
  }
}

.inspector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.inspector-header h3 {
  margin: 0;
  font-size: 1rem;
}

.inspector-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.signals-status {
  margin: 0;
  color: #444;
  font-size: 0.9rem;
}

.inspector-signal-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.inspector-signal-block {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
}

.inspector-signal-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
}

.inspector-signal-description {
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
}

.switch-dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
}

.switch-dialog {
  background: white;
  border-radius: 10px;
  padding: 1.2rem;
  max-width: 360px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.switch-dialog p {
  margin: 0 0 1rem;
  font-size: 0.9rem;
  color: #333;
}

.switch-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.switch-dialog-save-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.switch-dialog-discard-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-discard-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.switch-dialog-cancel-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #444;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-cancel-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
