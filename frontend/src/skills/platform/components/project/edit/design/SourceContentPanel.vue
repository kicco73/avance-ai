<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Papa from 'papaparse'
import { TabulatorFull as Tabulator } from 'tabulator-tables'
import 'tabulator-tables/dist/css/tabulator.min.css'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile, postSourceWebImport } from '../../../../api.js'
import { promptDialog } from '../../../../../../dialogStore.js'
import { parseCsvRows } from '../../../../csvTable.js'
import { csvColumn, rowControlColumn, ROW_CONTROL_FIELD } from './csvCells.js'

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
const saveFailed = ref(false)
const tableHost = ref(null)
const fileInputRef = ref(null)

const content = ref('')
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)
const webImportLabel = computed(() => `${Math.round(webImportProgress.value ?? 0)}%`)

let table = null
let requestToken = 0
let pendingSave = false

function columnsFor(fields) {
  return [rowControlColumn(deleteRow), ...fields.map((field) => csvColumn(field, { onDelete: deleteColumn }))]
}

function serializeTable() {
  if (!table) return content.value
  const columns = table.getColumns().map((col) => col.getField()).filter((field) => field !== ROW_CONTROL_FIELD)
  return Papa.unparse({ fields: columns, data: table.getData() })
}

function buildTable(text) {
  const { fields, data } = parseCsvRows(text)
  table = new Tabulator(tableHost.value, {
    data,
    columns: columnsFor(fields),
    layout: 'fitData',
    height: '100%',
    reactiveData: false
  })
  table.on('cellEdited', () => {
    content.value = serializeTable()
    save()
  })
}

function setTableData(text) {
  const { fields, data } = parseCsvRows(text)
  table.setColumns(columnsFor(fields))
  table.setData(data)
}

async function deleteRow(row) {
  await row.delete()
  content.value = serializeTable()
  await save()
}

async function deleteColumn(column) {
  const dataColumns = table.getColumns().filter((col) => col.getField() !== ROW_CONTROL_FIELD)
  if (dataColumns.length <= 1) return
  await column.delete()
  content.value = serializeTable()
  await save()
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
  if (saving.value) {
    pendingSave = true
    return false
  }
  saving.value = true
  saveFailed.value = false
  let ok
  try {
    const result = await putProjectFile(props.projectId, props.fileName, content.value)
    content.value = result.content
    originalContent.value = result.content
    canUndo.value = result.can_undo
    canRedo.value = result.can_redo
    setTableData(result.content)
    emit('saved', result)
    ok = true
  } catch {
    saveFailed.value = true
    ok = false
  } finally {
    saving.value = false
  }
  if (pendingSave) {
    pendingSave = false
    await save()
  }
  return ok
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
  await save()
}

async function addColumn() {
  if (!table) return
  const name = await promptDialog({
    title: 'Add column',
    body: 'Name of the new column.',
    placeholder: 'column name',
    okLabel: 'Add'
  })
  const field = name?.trim()
  if (!field) return
  if (table.getColumns().some((col) => col.getField() === field)) return
  await table.addColumn(csvColumn(field, { onDelete: deleteColumn }))
  content.value = serializeTable()
  await save()
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
      <div class="source-content-toolbar-left">
        <button
          class="source-content-ai-btn"
          :class="{ 'source-content-ai-btn-running': webImporting }"
          :disabled="webImporting || loading || saving || uploading"
          :data-tooltip="webImporting ? `Importing… ${webImportLabel}` : 'Import from web search'"
          @click="webImport"
        >
          <span v-if="webImporting" class="source-content-ai-progress">{{ webImportLabel }}</span>
          <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
          </svg>
        </button>
        <button class="add-row-btn" :disabled="loading || saving" data-tooltip="Add row" @click="addRow">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M4 4h16v2H4V4zm0 4h16v2H4V8zm7 6h2v3h3v2h-3v3h-2v-3H8v-2h3v-3z" />
          </svg>
        </button>
        <button class="add-column-btn" :disabled="loading || saving" data-tooltip="Add column" @click="addColumn">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M4 4h2v16H4V4zm4 0h2v16H8V4zm6 7h2v-3h2v3h3v2h-3v3h-2v-3h-2v-2z" />
          </svg>
        </button>
      </div>
      <div class="source-content-toolbar-actions">
        <button
          class="undo-redo-btn"
          data-tooltip="Undo"
          :disabled="loading || saving || !canUndo"
          @click="undo"
        >↺</button>
        <button
          class="undo-redo-btn"
          data-tooltip="Redo"
          :disabled="loading || saving || !canRedo"
          @click="redo"
        >↻</button>
        <button
          class="source-content-download-btn"
          :disabled="loading"
          data-tooltip="Download"
          @click="download"
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z" />
          </svg>
        </button>
        <button
          class="source-content-upload-btn"
          :disabled="uploading || saving"
          :data-tooltip="uploading ? 'Uploading…' : 'Upload'"
          @click="triggerUpload"
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z" />
          </svg>
        </button>
        <input ref="fileInputRef" type="file" accept=".csv" class="source-content-upload-input" @change="handleUpload" />
        <span v-if="saving" class="source-content-save-status">Saving…</span>
        <span v-else-if="saveFailed" class="source-content-save-status source-content-save-status-failed">Save failed — retry the edit</span>
      </div>
    </div>

    <p v-if="loading" class="source-content-status">Loading…</p>
    <div v-show="!loading" ref="tableHost" class="source-content-table"></div>
  </div>
</template>

<style scoped>
.source-content-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.source-content-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.source-content-toolbar-left { display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; }
.source-content-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; }
[data-tooltip] { position: relative; }
[data-tooltip]::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.1s ease 0.1s;
  z-index: 1000;
}
.source-content-toolbar-left [data-tooltip]::after { left: 0; transform: none; }
.source-content-toolbar-actions [data-tooltip]::after { left: auto; right: 0; transform: none; }
[data-tooltip]:hover::after, [data-tooltip]:focus-visible::after { opacity: 1; }
.source-content-status { margin: 0; padding: 1rem; color: #444; }
.source-content-table { flex: 1; min-height: 0; overflow: auto; }
.source-content-table :deep(.tabulator),
.source-content-table :deep(.tabulator-tableholder) { background-color: #e2e2e2 !important; }
.source-content-table :deep(.csv-cell-editor) { position: relative; height: 100%; }
.source-content-table :deep(.csv-cell-editor-switch) { position: absolute; top: 2px; right: 2px; width: 1.3rem; height: 1.3rem; padding: 0; border-radius: 4px; border: 1px solid #ccc; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.8rem; line-height: 1; }
.source-content-table :deep(.csv-cell-editor-switch:hover) { background: #eef2f9; }
.undo-redo-btn { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.9rem; }
.undo-redo-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.add-row-btn, .add-column-btn { display: flex; align-items: center; justify-content: center; width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 6px; border: 1px solid #ccc; background: white; color: #4a6fa5; cursor: pointer; }
.add-row-btn:hover:not(:disabled), .add-column-btn:hover:not(:disabled) { background: #eef2f9; border-color: #4a6fa5; }
.add-row-btn:disabled, .add-column-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-download-btn, .source-content-upload-btn { display: flex; align-items: center; justify-content: center; width: 1.8rem; height: 1.8rem; padding: 0; border-radius: 6px; border: 1px solid #ccc; background: white; color: #4a6fa5; cursor: pointer; }
.source-content-download-btn:hover:not(:disabled), .source-content-upload-btn:hover:not(:disabled) { background: #eef2f9; border-color: #4a6fa5; }
.source-content-download-btn:disabled, .source-content-upload-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-table :deep(.csv-column-title) { margin-left: 0.3rem; }
.source-content-table :deep(.tabulator-col-title) { display: flex; align-items: center; }
.source-content-table :deep(.csv-column-delete-btn),
.source-content-table :deep(.csv-row-delete-btn) { width: 1.4rem; height: 1.4rem; padding: 0; border: none; border-radius: 6px; background: none; color: #777; font-size: 1.1rem; line-height: 1; cursor: pointer; flex-shrink: 0; }
.source-content-table :deep(.csv-column-delete-btn:hover),
.source-content-table :deep(.csv-row-delete-btn:hover) { background: #f0f0f0; }
.source-content-upload-input { display: none; }
.source-content-ai-btn { display: flex; align-items: center; justify-content: center; min-width: 1.8rem; height: 1.8rem; padding: 0 0.4rem; border-radius: 6px; border: 1px solid #ccc; background: white; color: #8b5cf6; cursor: pointer; }
.source-content-ai-btn:hover:not(:disabled) { background: #f5f0fe; border-color: #8b5cf6; }
.source-content-ai-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.source-content-ai-btn-running { border-color: #8b5cf6; background: #f5f0fe; opacity: 1; }
.source-content-ai-progress { font-size: 0.75rem; font-variant-numeric: tabular-nums; }
.source-content-save-status { font-size: 0.82rem; color: #666; }
.source-content-save-status-failed { color: #c0392b; }
</style>
