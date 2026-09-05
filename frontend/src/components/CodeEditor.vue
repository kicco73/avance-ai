<script setup>
// CodeMirror-backed editor for one project file. Scoped to a single
// `fileName` for its lifetime — callers remount via :key="fileName" when
// switching files, so a stale in-flight fetch can't leak across files.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { HighlightStyle, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import { yaml, yamlLanguage } from '@codemirror/lang-yaml'
import { css, cssLanguage } from '@codemirror/lang-css'
import { markdown } from '@codemirror/lang-markdown'
import { csv } from '../csvLanguage.js'
import { cssColorPicker } from './cssColorPicker.js'
import { cssUrlCompletionSource } from './cssUrlCompletion.js'
import { yamlAttachmentCompletionSource } from './yamlAttachmentCompletion.js'
import { yamlStructureCompletionSource } from './yamlStructureCompletion.js'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile } from '../api.js'

// @lezer/yaml tags every plain scalar (unquoted values, `|`/`>` block
// bodies) as tags.content, which defaultHighlightStyle leaves uncolored —
// so most of index.yml's actual text renders black-on-white. Registering
// this as another {fallback: true} style alongside basicSetup's own
// wouldn't add it: CodeMirror's fallback slot keeps only the first
// registration and drops the rest. Re-registering defaultHighlightStyle
// as a *non-fallback* style here instead (see createEditor's yaml
// branch) works because non-fallback styles union with each other.
const yamlValueHighlightStyle = HighlightStyle.define([
  { tag: [tags.content, tags.attributeValue], color: '#8b5c00' }
])

const props = defineProps({
  projectId: { type: String, required: true },
  fileName: { type: String, required: true },
  // Basenames url(...) can complete to — the Theme branch's image assets.
  // Only meaningful for a text/css buffer; ignored otherwise.
  cssAssetFiles: { type: Array, default: () => [] },
  // Basenames an `attachments:` entry can complete to — the Behavior
  // branch's own attachments (index.yml/index.css/Theme assets excluded).
  // Only meaningful for index.yml's text/yaml buffer; ignored otherwise.
  yamlAttachmentFiles: { type: Array, default: () => [] },
  // The project's current draft revision, for save()'s own build-error
  // handling below — a failed build's own `fields.revision` (see
  // AutomatonBuildError) is only safe to jump to while it still matches
  // this; a stale one (another save/publish/revert landed meanwhile)
  // suppresses the jump instead of pointing at a line that may no
  // longer mean what the error said. Only meaningful for index.yml;
  // null for every other file, where no build error can occur at all.
  currentRevision: { type: Number, default: null }
})

const emit = defineEmits(['saved', 'renamed', 'build-error'])

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
  // text/plain covers .txt attachments — MdEditorPanel.vue treats them the
  // same as .md, so its CodeEditor gets the same language mode too.
  else if (contentType.value === 'text/markdown' || contentType.value === 'text/plain') {
    extensions.splice(1, 0, markdown())
  }
  // A generic .csv attachment uploaded via FileExplorer.vue (a source's
  // own sources/<id>.csv gets SourceContentPanel.vue's Tabulator grid
  // instead, never this generic fallback).
  else if (contentType.value === 'text/csv') {
    extensions.splice(1, 0, csv())
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
    const file = await getProjectFile(props.projectId, props.fileName)
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
    const result = await putProjectFile(props.projectId, props.fileName, content.value)
    // Refresh from the server's response rather than trusting what was typed.
    setEditorDoc(result.content)
    originalContent.value = result.content
    canUndo.value = result.can_undo
    canRedo.value = result.can_redo
    emit('saved', result)
    return true
  } catch (err) {
    // A build error names exactly this project/file — jump-worthy only
    // while `currentRevision` (the draft this save() was actually
    // attempted against) still matches what the backend built: a save/
    // publish/revert landing in between means the line may no longer
    // mean what the error said, so the jump is suppressed rather than
    // risking pointing at the wrong place. See AutomatonBuildError.
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

// Reverts to the last loaded/saved content, discarding local edits —
// used by the unsaved-changes dialog when the user picks "Discard".
function discard() {
  content.value = originalContent.value
  setEditorDoc(originalContent.value)
}

// Replaces the buffer with externally-produced text (e.g. the index.yml
// editor's AI button) — left dirty against originalContent, same as a
// normal keystroke edit, so Save picks it up.
function setContent(newContent) {
  content.value = newContent
  setEditorDoc(newContent)
}

// Previews the previous/next content from history without persisting —
// no 'saved' emitted. `originalContent` stays put, so the resulting
// diff from it is what re-enables Save. A rename step (file.renamed_to
// set — see db/history.py's own rename-marker) has no content of *this*
// file's own to preview: this instance is scoped to `props.fileName` for
// its whole lifetime (see the module comment up top), so it can't just
// keep going under a different name — 'renamed' tells the parent to
// remount a fresh instance at renamed_to instead.
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

defineExpose({ content, isDirty, canUndo, canRedo, mediaType, contentType, loading, saving, save, discard, setContent, undo, redo, jumpToLine, reload })

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
