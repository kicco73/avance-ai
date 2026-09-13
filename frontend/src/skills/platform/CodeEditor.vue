<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment, StateEffect, StateField } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { Decoration, keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { HighlightStyle, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import { yaml, yamlLanguage } from '@codemirror/lang-yaml'
import { css, cssLanguage } from '@codemirror/lang-css'
import { markdown } from '@codemirror/lang-markdown'
import { csv } from './csvLanguage.js'
import { cssColorPicker } from './cssColorPicker.js'
import { cssUrlCompletionSource } from './cssUrlCompletion.js'
import { yamlAttachmentCompletionSource } from './yamlAttachmentCompletion.js'
import { yamlStructureCompletionSource } from './yamlStructureCompletion.js'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile } from './api.js'

const yamlValueHighlightStyle = HighlightStyle.define([
  { tag: [tags.content, tags.attributeValue], color: '#8b5c00' }
])

const setErrorLine = StateEffect.define()
const errorLineDecoration = Decoration.line({ class: 'cm-error-line' })
const errorLineField = StateField.define({
  create: () => Decoration.none,
  update(decorations, transaction) {
    let next = decorations.map(transaction.changes)
    for (const effect of transaction.effects) {
      if (!effect.is(setErrorLine)) continue
      if (effect.value === null) {
        next = Decoration.none
      } else {
        const lineInfo = transaction.state.doc.line(effect.value + 1)
        next = Decoration.set([errorLineDecoration.range(lineInfo.from)])
      }
    }
    return next
  },
  provide: (field) => EditorView.decorations.from(field)
})

const props = defineProps({
  projectId: { type: String, required: true },
  fileName: { type: String, required: true },
  cssAssetFiles: { type: Array, default: () => [] },
  yamlAttachmentFiles: { type: Array, default: () => [] },
  currentRevision: { type: Number, default: null }
})

const emit = defineEmits(['saved', 'renamed', 'build-error', 'loaded'])

const loading = ref(true)
const saving = ref(false)
const editorHost = ref(null)

const content = ref('')
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)

const canUndo = ref(false)
const canRedo = ref(false)

const mediaType = ref(null)

const contentType = ref(null)

let view = null
const editableCompartment = new Compartment()

let requestToken = 0

function createEditor(doc) {
  const extensions = [
    basicSetup,
    EditorView.lineWrapping,
    errorLineField,
    editableCompartment.of(EditorView.editable.of(true)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) content.value = update.state.doc.toString()
    }),
    keymap.of([
      {
        key: 'Mod-s',
        run: () => {
          if (!loading.value && !saving.value && isDirty.value) save()
          return true
        }
      },
      indentWithTab
    ])
  ]
  if (contentType.value === 'text/yaml') {
    extensions.splice(
      1, 0,
      yaml(),
      syntaxHighlighting(defaultHighlightStyle),
      syntaxHighlighting(yamlValueHighlightStyle),
      yamlLanguage.data.of({ autocomplete: yamlAttachmentCompletionSource(() => props.yamlAttachmentFiles) }),
      yamlLanguage.data.of({ autocomplete: yamlStructureCompletionSource() })
    )
  }
  else if (contentType.value === 'text/css') {
    extensions.splice(
      1, 0,
      css(),
      cssColorPicker,
      cssLanguage.data.of({ autocomplete: cssUrlCompletionSource(() => props.cssAssetFiles) })
    )
  }
  else if (contentType.value === 'text/markdown' || contentType.value === 'text/plain') {
    extensions.splice(1, 0, markdown())
  }
  else if (contentType.value === 'text/csv') {
    extensions.splice(1, 0, csv())
  }
  view = new EditorView({ doc, extensions, parent: editorHost.value })
}

function destroyEditor() {
  view?.destroy()
  view = null
}

function setEditorDoc(newContent) {
  if (!view) return
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: newContent }, effects: setErrorLine.of(null) })
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
    mediaType.value = file?.media_type ?? 'text/css'
    contentType.value = file?.content_type ?? 'text/css'
  } catch {
    if (token === requestToken) loading.value = false
    return
  }
  loading.value = false
  if (view) {
    setEditorDoc(content.value)
    emit('loaded')
    return
  }
  await nextTick()
  if (token !== requestToken) return
  createEditor(content.value)
  emit('loaded')
}

async function save() {
  saving.value = true
  try {
    const result = await putProjectFile(props.projectId, props.fileName, content.value)
    setEditorDoc(result.content)
    originalContent.value = result.content
    canUndo.value = result.can_undo
    canRedo.value = result.can_redo
    emit('saved', result)
    return true
  } catch (err) {
    const fields = err?.fields
    if (
      fields?.project_id === props.projectId && fields?.file === props.fileName &&
      fields?.line != null && fields?.revision === props.currentRevision
    ) {
      emit('build-error', fields.line)
    }
    return false
  } finally {
    saving.value = false
  }
}

function discard() {
  content.value = originalContent.value
  setEditorDoc(originalContent.value)
}

function setContent(newContent) {
  content.value = newContent
  setEditorDoc(newContent)
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

function jumpToLine(lineIndex) {
  if (!view) return
  if (lineIndex < 0 || lineIndex >= view.state.doc.lines) return
  const lineInfo = view.state.doc.line(lineIndex + 1)
  view.dispatch({
    selection: { anchor: lineInfo.from, head: lineInfo.from },
    effects: EditorView.scrollIntoView(lineInfo.from, { y: 'center' })
  })
  view.focus()
}

function markErrorLine(lineIndex) {
  if (!view) return
  if (lineIndex < 0 || lineIndex >= view.state.doc.lines) return
  view.dispatch({ effects: setErrorLine.of(lineIndex) })
}

async function reload() {
  await load()
}

defineExpose({ content, isDirty, canUndo, canRedo, mediaType, contentType, loading, saving, save, discard, setContent, undo, redo, jumpToLine, markErrorLine, reload })

watch(saving, (isSaving) => {
  view?.dispatch({ effects: editableCompartment.reconfigure(EditorView.editable.of(!isSaving)) })
})

onMounted(load)
onBeforeUnmount(destroyEditor)
</script>

<template>
  <div class="code-editor">
    <p v-if="loading" class="code-editor-status">Loading…</p>
    <div v-show="!loading" ref="editorHost" class="code-editor-host"></div>
  </div>
</template>

<style scoped>
.code-editor { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.code-editor-status { margin: 0; padding: 1rem; color: #444; }
.code-editor-host { flex: 1; min-height: 0; overflow: auto; }
.code-editor-host :deep(.cm-error-line),
.code-editor-host :deep(.cm-activeLine.cm-error-line) { background: rgba(220, 38, 38, 0.32); }
</style>
