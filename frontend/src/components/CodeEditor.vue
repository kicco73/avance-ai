<script setup>
// CodeMirror-backed editor for one project file. Scoped to a single
// `fileName` for its lifetime — callers remount via :key="fileName" when
// switching files, so a stale in-flight fetch can't leak across files.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { yaml } from '@codemirror/lang-yaml'
import { css, cssLanguage } from '@codemirror/lang-css'
import { markdown } from '@codemirror/lang-markdown'
import { cssColorPicker } from './cssColorPicker.js'
import { cssUrlCompletionSource } from './cssUrlCompletion.js'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile } from '../api.js'

const props = defineProps({
  projectName: { type: String, required: true },
  fileName: { type: String, required: true },
  // Basenames url(...) can complete to — the Theme branch's image assets.
  // Only meaningful for a text/css buffer; ignored otherwise.
  cssAssetFiles: { type: Array, default: () => [] }
})

const emit = defineEmits(['saved'])

const loading = ref(true)
const saving = ref(false)
const editorHost = ref(null)

const content = ref('')
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)

// Backend-decided, refreshed on every load/save/undo/redo — this
// component never tracks version numbers itself.
const canUndo = ref(false)
const canRedo = ref(false)

// Extension-based, fixed for this file's lifetime — set once on load.
const mediaType = ref(null)

// Persisted content type; determines CodeMirror's language mode below.
// Set once on load, same as mediaType.
const contentType = ref(null)

let view = null
const editableCompartment = new Compartment()

// Guards this instance's own out-of-order async responses (e.g. a rapid
// double Undo click) — not cross-file races, already ruled out by the
// :key="fileName" remount.
let requestToken = 0

function createEditor(doc) {
  const extensions = [
    basicSetup,
    EditorView.lineWrapping,
    editableCompartment.of(EditorView.editable.of(true)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) content.value = update.state.doc.toString()
    }),
    // Ctrl/Cmd-S — mirrors the toolbar Save button's guard, and always
    // swallows the key so the browser's native "Save page" never opens.
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
  if (contentType.value === 'text/yaml') extensions.splice(1, 0, yaml())
  else if (contentType.value === 'text/css') {
    extensions.splice(
      1, 0,
      css(),
      cssColorPicker,
      cssLanguage.data.of({ autocomplete: cssUrlCompletionSource(() => props.cssAssetFiles) })
    )
  }
  // text/plain covers .txt attachments — MdEditorPanel.vue treats them the
  // same as .md, so its CodeEditor gets the same language mode too.
  else if (contentType.value === 'text/markdown' || contentType.value === 'text/plain') {
    extensions.splice(1, 0, markdown())
  }
  view = new EditorView({ doc, extensions, parent: editorHost.value })
}

function destroyEditor() {
  view?.destroy()
  view = null
}

// Replaces the editor's whole document (undo/redo, post-save refresh).
// `content` updates itself via the updateListener; never set directly.
function setEditorDoc(newContent) {
  if (!view) return
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: newContent } })
}

async function load() {
  const token = ++requestToken
  loading.value = true
  try {
    const file = await getProjectFile(props.projectName, props.fileName)
    if (token !== requestToken) return
    // A 204 (null) response means the file doesn't exist yet (e.g.
    // index.css is optional) — not an error; start with an empty buffer.
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
  // A reload with a view already mounted: replace its buffer in place
  // rather than creating a second EditorView (createEditor runs once,
  // only on the very first load).
  if (view) {
    setEditorDoc(content.value)
    return
  }
  await nextTick() // editorHost is v-show'd by loading above — wait a tick for layout to settle
  if (token !== requestToken) return
  createEditor(content.value)
}

// Persistence only, no navigation — callers stay open regardless of
// outcome. Returns success; failures are surfaced via the shared error
// store (see api.js's apiFetch).
async function save() {
  saving.value = true
  try {
    const result = await putProjectFile(props.projectName, props.fileName, content.value)
    // Refresh from the server's response rather than trusting what was typed.
    setEditorDoc(result.content)
    originalContent.value = result.content
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

// Reverts to the last loaded/saved content, discarding local edits —
// used by the unsaved-changes dialog when the user picks "Discard".
function discard() {
  content.value = originalContent.value
  setEditorDoc(originalContent.value)
}

// Previews the previous/next content from history without persisting —
// no 'saved' emitted. `originalContent` stays put, so the resulting
// diff from it is what re-enables Save.
async function applyHistoryNavigation(action) {
  const token = ++requestToken
  try {
    const file = await action(props.projectName, props.fileName, content.value)
    if (token !== requestToken) return
    setEditorDoc(file.content)
    canUndo.value = file.can_undo
    canRedo.value = file.can_redo
  } catch {
    // already surfaced via apiFetch
  }
}

function undo() {
  if (canUndo.value) applyHistoryNavigation(undoProjectFile)
}

function redo() {
  if (canRedo.value) applyHistoryNavigation(redoProjectFile)
}

// Moves the cursor to a 0-based line and focuses the editor. Best-effort:
// a lineIndex past the document's end is ignored.
function jumpToLine(lineIndex) {
  if (!view) return
  if (lineIndex < 0 || lineIndex >= view.state.doc.lines) return
  const lineInfo = view.state.doc.line(lineIndex + 1) // CodeMirror lines are 1-based
  view.dispatch({
    selection: { anchor: lineInfo.from, head: lineInfo.from },
    effects: EditorView.scrollIntoView(lineInfo.from, { y: 'center' })
  })
  view.focus()
}

// Re-fetches this file's content and replaces the live buffer — for
// callers that changed the file through some other path. Caller must
// ensure there are no unsaved local edits first; this always overwrites.
async function reload() {
  await load()
}

defineExpose({ content, isDirty, canUndo, canRedo, mediaType, contentType, loading, saving, save, discard, undo, redo, jumpToLine, reload })

// Read-only while a save is in flight — typing over content that's
// about to be overwritten by the save response would be silently lost.
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
</style>
