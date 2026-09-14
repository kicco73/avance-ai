<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Crepe } from '@milkdown/crepe'
import { remarkStringifyOptionsCtx } from '@milkdown/kit/core'
import { replaceAll } from '@milkdown/kit/utils'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'
import DocInfoButton from '../../components/DocInfoButton.vue'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile } from './api.js'

const props = defineProps({
  projectId: { type: String, required: true },
  fileName: { type: String, required: true }
})

const emit = defineEmits(['saved', 'renamed', 'loaded'])

const loading = ref(true)
const saving = ref(false)
const editorHost = ref(null)
const topBarActions = ref(null)

const content = ref('')
const originalContent = ref('')
const isDirty = computed(() => content.value.trimEnd() !== originalContent.value.trimEnd())

const canUndo = ref(false)
const canRedo = ref(false)

let crepe = null
let requestToken = 0

async function createEditor(doc) {
  crepe = new Crepe({
    root: editorHost.value,
    defaultValue: doc,
    features: { [Crepe.Feature.Latex]: false, [Crepe.Feature.TopBar]: true }
  })
  crepe.editor.config((ctx) => { ctx.update(remarkStringifyOptionsCtx, (options) => ({ ...options, bullet: '-' })) })
  crepe.on((listener) => {
    listener.markdownUpdated((_ctx, markdown) => { content.value = markdown })
  })
  await crepe.create()
  content.value = crepe.getMarkdown()
  originalContent.value = content.value
  const slot = document.createElement('div')
  slot.className = 'markdown-editor-actions'
  editorHost.value.querySelector('.milkdown-top-bar').appendChild(slot)
  topBarActions.value = slot
}

function destroyEditor() {
  topBarActions.value = null
  crepe?.destroy()
  crepe = null
}

function setEditorDoc(newContent) {
  if (!crepe) return
  crepe.editor.action(replaceAll(newContent, true))
  content.value = crepe.getMarkdown()
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
  if (crepe) {
    setEditorDoc(content.value)
    originalContent.value = content.value
    emit('loaded')
    return
  }
  await nextTick()
  if (token !== requestToken) return
  await createEditor(content.value)
  emit('loaded')
}

async function save() {
  saving.value = true
  const text = content.value
  try {
    const result = await putProjectFile(props.projectId, props.fileName, text)
    originalContent.value = text
    canUndo.value = result.can_undo
    canRedo.value = result.can_redo
    emit('saved', result)
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

function discard() {
  setEditorDoc(originalContent.value)
  originalContent.value = content.value
}

async function applyHistoryNavigation(action) {
  const token = ++requestToken
  try {
    const file = await action(props.projectId, props.fileName, content.value)
    if (token !== requestToken) return
    if (file.renamed_to) {
      emit('renamed', file.renamed_to)
      return
    }
    setEditorDoc(file.content)
    originalContent.value = content.value
    canUndo.value = file.can_undo
    canRedo.value = file.can_redo
  } catch {
  }
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

function onKeydown(event) {
  if (!(event.key === 's' && (event.metaKey || event.ctrlKey))) return
  event.preventDefault()
  if (!loading.value && !saving.value && isDirty.value) save()
}

defineExpose({ content, isDirty, canUndo, canRedo, loading, saving, save, discard, undo, redo, reload })

onMounted(load)
onBeforeUnmount(destroyEditor)
</script>

<template>
  <div class="markdown-editor" @keydown="onKeydown">
    <p v-if="loading" class="markdown-editor-status">Loading…</p>
    <div v-show="!loading" ref="editorHost" class="markdown-editor-host"></div>
    <Teleport v-if="topBarActions" :to="topBarActions">
      <button
        class="markdown-editor-history-btn"
        title="Undo"
        :disabled="saving || !canUndo"
        @mousedown.prevent
        @click="undo"
      >↺</button>
      <button
        class="markdown-editor-history-btn"
        title="Redo"
        :disabled="saving || !canRedo"
        @mousedown.prevent
        @click="redo"
      >↻</button>
      <button
        class="markdown-editor-save-btn"
        :disabled="saving || !isDirty"
        @mousedown.prevent
        @click="save"
      >{{ saving ? 'Saving…' : 'Save' }}</button>
      <DocInfoButton doc-name="markdown-guide" title="Markdown syntax" />
    </Teleport>
  </div>
</template>

<style scoped>
.markdown-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.markdown-editor-status { margin: 0; padding: 1rem; color: #444; }
.markdown-editor-host { flex: 1; min-height: 0; overflow: auto; }
.markdown-editor-host :deep(.milkdown) { min-height: 100%; --crepe-base-font-size: 15px; --crepe-font-default: inherit; --crepe-font-title: inherit; }
.markdown-editor-host :deep(.milkdown .milkdown-top-bar) { flex-wrap: nowrap; gap: 0.5rem; padding-right: 0.75rem; }
.markdown-editor-host :deep(.milkdown .milkdown-top-bar .top-bar-inner) { width: auto; flex: 1; min-width: 0; }
.markdown-editor-host :deep(.markdown-editor-actions) { display: flex; align-items: center; gap: 0.4rem; flex-shrink: 0; margin-left: auto; }
.markdown-editor-host :deep(.milkdown .ProseMirror) { padding: 0.75rem 1.5rem 3rem; min-height: 100%; }
.markdown-editor-host :deep(.milkdown img) { max-width: 100%; }
.markdown-editor-host :deep(.milkdown span[data-type="hardbreak"][data-is-inline="true"]) { display: block; height: 0; line-height: 0; }
.markdown-editor-history-btn { height: 32px; padding: 0 0.6rem; border-radius: 4px; border: 1px solid var(--crepe-color-outline); background: var(--crepe-color-background); cursor: pointer; font-size: 0.9rem; color: var(--crepe-color-on-surface); }
.markdown-editor-history-btn:hover:not(:disabled) { background: var(--crepe-color-hover); }
.markdown-editor-history-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.markdown-editor-save-btn { height: 32px; padding: 0 1rem; border-radius: 4px; border: 1px solid #2e7d32; background: #2e7d32; color: white; cursor: pointer; font-size: 0.85rem; }
.markdown-editor-save-btn:hover:not(:disabled) { background: #256428; }
.markdown-editor-save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
