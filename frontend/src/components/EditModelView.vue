<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { getModelFile, putModelFile } from '../api.js'
import { clearApiError, errorDetail, errorMessage } from '../errorStore.js'

const props = defineProps({
  modelName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close', 'saved'])

const loading = ref(true)
const saving = ref(false)
const showErrorDetail = ref(false)
const editorHost = ref(null)

// The editor's own doc is the source of truth for content while it's
// mounted — this ref only mirrors it (via the updateListener below) so
// save() has something to send without querying the view directly.
const content = ref('')

let view = null
const editableCompartment = new Compartment()

function createEditor(doc) {
  view = new EditorView({
    doc,
    extensions: [
      basicSetup,
      yaml(),
      EditorView.lineWrapping,
      editableCompartment.of(EditorView.editable.of(true)),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) content.value = update.state.doc.toString()
      })
    ],
    parent: editorHost.value
  })
}

async function load() {
  loading.value = true
  clearApiError()
  try {
    content.value = await getModelFile(props.modelName)
  } catch {
    // already surfaced via apiFetch
    loading.value = false
    return
  }
  loading.value = false
  await nextTick() // editorHost is v-show, but wait a tick anyway for layout to settle
  createEditor(content.value)
}

// On failure the shared error store already has the message (apiFetch set
// it) — the view stays open with the editor's content untouched so the
// user can fix their edit and retry without having lost anything typed.
async function save() {
  saving.value = true
  clearApiError()
  try {
    await putModelFile(props.modelName, content.value)
    emit('saved')
    emit('close')
  } catch {
    // already surfaced via apiFetch
  } finally {
    saving.value = false
  }
}

watch(saving, (isSaving) => {
  view?.dispatch({ effects: editableCompartment.reconfigure(EditorView.editable.of(!isSaving)) })
})

onMounted(load)
onBeforeUnmount(() => view?.destroy())
</script>

<template>
  <div class="edit-model-overlay">
    <div class="edit-model-header">
      <h2>Edit model — {{ modelName }}</h2>
      <div class="edit-model-header-actions">
        <button class="save-btn" :disabled="loading || saving" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <div v-if="errorMessage" class="edit-model-error-row">
      <p class="edit-model-error">{{ errorMessage }}</p>
      <button
        v-if="errorDetail"
        type="button"
        class="edit-model-error-details-btn"
        @click="showErrorDetail = !showErrorDetail"
      >
        {{ showErrorDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>
    <pre v-if="errorMessage && errorDetail && showErrorDetail" class="edit-model-error-detail">{{ errorDetail }}</pre>

    <div class="edit-model-body">
      <p v-if="loading" class="edit-model-status">Loading…</p>
      <div v-show="!loading" ref="editorHost" class="edit-model-editor"></div>
    </div>
  </div>
</template>

<style scoped>
.edit-model-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.edit-model-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.edit-model-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.edit-model-header-actions {
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

.edit-model-error-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
}

.edit-model-error {
  margin: 0;
  color: #c62828;
  font-size: 0.9rem;
  flex: 1;
}

.edit-model-error-details-btn {
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.8rem;
}

.edit-model-error-detail {
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

.edit-model-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.edit-model-status {
  margin: auto;
  color: #444;
}

.edit-model-editor {
  flex: 1;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.edit-model-editor :deep(.cm-editor) {
  height: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
}

.edit-model-editor :deep(.cm-scroller) {
  overflow: auto;
  line-height: 1.5;
}

.edit-model-editor :deep(.cm-editor.cm-focused) {
  outline: none;
}
</style>
