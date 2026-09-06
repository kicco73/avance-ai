<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Papa from 'papaparse'
import { TabulatorFull as Tabulator } from 'tabulator-tables'
import 'tabulator-tables/dist/css/tabulator.min.css'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile, postSourceWebImport } from '../../../../api.js'
import { promptDialog } from '../../../../dialogStore.js'

const props = defineProps({
  projectId: { type: String, required: true },
  fileName: { type: String, required: true },
  sourceName: { type: String, required: true }
})

const emit = defineEmits(['saved'])

const loading = ref(true)
const saving = ref(false)
const uploading = ref(false)
const webImporting = ref(false)
const webImportProgress = ref(null)
const canUndo = ref(false)
const canRedo = ref(false)
const tableHost = ref(null)
const fileInputRef = ref(null)

const content = ref('')
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)
const webImportLabel = computed(() => `${Math.round(webImportProgress.value ?? 0)}%`)

let table = null
let requestToken = 0

function parseCsv(text) {
  const result = Papa.parse(text ?? '', { header: true, skipEmptyLines: true })
  const fields = result.meta.fields ?? []
  const columns = fields.map((field) => ({ title: field, field, editor: 'input' }))
  return { columns, data: result.data }
}

function serializeTable() {
  if (!table) return content.value
  const columns = table.getColumns().map((col) => col.getField())
  return Papa.unparse({ fields: columns, data: table.getData() })
}

function buildTable(text) {
  const { columns, data } = parseCsv(text)
  table = new Tabulator(tableHost.value, {
    data,
    columns,
    layout: 'fitDataStretch',
    height: '100%',
    reactiveData: false
  })
  table.on('cellEdited', () => {
    content.value = serializeTable()
  })
}

function setTableData(text) {
  const { columns, data } = parseCsv(text)
  table.setColumns(columns)
  table.setData(data)
}

async function load() {
  const token = ++requestToken
  loading.value = true
  try {
    const file = await getProjectFile(props.projectId, props.fileName)
    if (token !== requestToken) return
    const fileContent = file?.content ?? ''
    content.value = fileContent
    originalContent.value = fileContent
    canUndo.value = file?.can_undo ?? false
    canRedo.value = file?.can_redo ?? false
  } catch {
    if (token === requestToken) loading.value = false
    return
  }
  loading.value = false
  if (table) {
    setTableData(content.value)
    return
  }
  await nextTick()
  if (token !== requestToken) return
  buildTable(content.value)
}

async function save() {
  saving.value = true
  try {
    const result = await putProjectFile(props.projectId, props.fileName, content.value)
    content.value = result.content
    originalContent.value = result.content
    canUndo.value = result.can_undo
    canRedo.value = result.can_redo
    setTableData(result.content)
    emit('saved', result)
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

function discard() {
  content.value = originalContent.value
  setTableData(originalContent.value)
}

async function applyHistoryNavigation(action) {
  const token = ++requestToken
  try {
    const file = await action(props.projectId, props.fileName, content.value)
    if (token !== requestToken) return
    content.value = file.content
    setTableData(file.content)
    canUndo.value = file.can_undo
    canRedo.value = file.can_redo
  } catch {}
}

function undo() {
  if (canUndo.value) applyHistoryNavigation(undoProjectFile)
}

function redo() {
  if (canRedo.value) applyHistoryNavigation(redoProjectFile)
}

async function reload() {
  await load()
}

async function addRow() {
  if (!table) return
  await table.addRow({})
  content.value = serializeTable()
}

async function webImport() {
  const query = await promptDialog({
    title: 'AI Web Import',
    body: 'Search the web and import what it finds into this source.',
    placeholder: 'e.g. well-reviewed dentists in Barcelona',
    okLabel: 'Search'
  })
  if (!query?.trim()) return
  webImporting.value = true
  webImportProgress.value = 0
  try {
    await postSourceWebImport(props.projectId, props.sourceName, query.trim(), (message) => {
      webImportProgress.value = message.percentage
    })
    await load()
  } catch {
    // already surfaced via apiFetch
  } finally {
    webImporting.value = false
    webImportProgress.value = null
  }
}

function triggerUpload() {
  fileInputRef.value?.click()
}

function download() {
  const blob = new Blob([content.value], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = props.fileName.slice(props.fileName.lastIndexOf('/') + 1)
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  uploading.value = true
  try {
    const text = await file.text()
    content.value = text
    setTableData(text)
    await save()
  } finally {
    uploading.value = false
  }
}

defineExpose({ content, isDirty, saving, save, discard, undo, redo, reload })

onMounted(load)
onBeforeUnmount(() => {
  table?.destroy()
  table = null
})
</script>

<template>
  <div class="source-content-panel">
    <div class="source-content-toolbar">
      <div class="source-content-toolbar-actions">
        <button
          class="source-content-ai-btn"
          :class="{ 'source-content-ai-btn-running': webImporting }"
          :disabled="webImporting || loading || saving || uploading"
          :title="webImporting ? `Importing from the web — ${webImportLabel}` : 'AI Web Import'"
          @click="webImport"
        >
          <span v-if="webImporting" class="source-content-ai-progress">{{ webImportLabel }}</span>
          <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
          </svg>
        </button>
        <button
          class="undo-redo-btn"
          title="Undo"
          :disabled="loading || saving || !canUndo"
          @click="undo"
        >↺</button>
        <button
          class="undo-redo-btn"
          title="Redo"
          :disabled="loading || saving || !canRedo"
          @click="redo"
        >↻</button>
        <button class="add-row-btn" :disabled="loading || saving" title="Add a row" @click="addRow">+ Row</button>
        <button
          class="source-content-download-btn"
          :disabled="loading"
          title="Download this source's CSV file"
          @click="download"
        >Download</button>
        <button
          class="source-content-upload-btn"
          :disabled="uploading || saving"
          title="Upload a CSV file, replacing this source's current content"
          @click="triggerUpload"
        >{{ uploading ? 'Uploading…' : 'Upload' }}</button>
        <input ref="fileInputRef" type="file" accept=".csv" class="source-content-upload-input" @change="handleUpload" />
        <button class="save-btn" :disabled="loading || saving || !isDirty" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </div>

    <p v-if="loading" class="source-content-status">Loading…</p>
    <div v-show="!loading" ref="tableHost" class="source-content-table"></div>
  </div>
</template>

<style scoped>
.source-content-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.source-content-toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.source-content-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; }
.source-content-status { margin: 0; padding: 1rem; color: #444; }
.source-content-table { flex: 1; min-height: 0; overflow: auto; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.add-row-btn, .source-content-download-btn, .source-content-upload-btn { padding: 0.35rem 0.7rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.82rem; }
.add-row-btn:hover:not(:disabled), .source-content-download-btn:hover:not(:disabled), .source-content-upload-btn:hover:not(:disabled) { background: #eef2f9; }
.add-row-btn:disabled, .source-content-download-btn:disabled, .source-content-upload-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-upload-input { display: none; }
.source-content-ai-btn { display: flex; align-items: center; justify-content: center; min-width: 1.8rem; height: 1.8rem; padding: 0 0.4rem; border-radius: 6px; border: 1px solid #ccc; background: white; color: #8b5cf6; cursor: pointer; }
.source-content-ai-btn:hover:not(:disabled) { background: #f5f0fe; border-color: #8b5cf6; }
.source-content-ai-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-ai-btn-running { border-color: #8b5cf6; background: #f5f0fe; opacity: 1; }
.source-content-ai-progress { font-size: 0.75rem; font-variant-numeric: tabular-nums; }
.save-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; }
.save-btn:hover:not(:disabled) { background: #256428; }
.save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
