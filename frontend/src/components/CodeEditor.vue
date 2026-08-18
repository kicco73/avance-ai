<script setup>
// CodeMirror-backed text editor for one project file — extracted out of
// EditProjectView.vue's own edit-project-editor-pane so the same editing
// experience (host DOM, createEditor, save/undo/redo, dirty tracking)
// mounts identically whether the caller is EditProjectView.vue itself
// (any file other than index.yml) or index.yml's own dedicated "code"
// segment (see that view's own docstring). Scoped to exactly one
// `fileName` for its whole lifetime — a caller switching which file is
// open remounts this component via :key="fileName" rather than this one
// watching its own prop, which is also what makes a rapid double-switch
// harmless on its own: a stale in-flight fetch from the *previous*
// mounted instance can only ever update that instance's own (by then
// orphaned, non-rendering) refs, never this one's.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { keymap } from '@codemirror/view'
import { yaml } from '@codemirror/lang-yaml'
import { getProjectFile, putProjectFile, undoProjectFile, redoProjectFile } from '../api.js'

const props = defineProps({
  projectName: { type: String, required: true },
  fileName: { type: String, required: true }
})

const emit = defineEmits(['saved'])

const YAML_PATTERN = /\.ya?ml$/i

const loading = ref(true)
const saving = ref(false)
const editorHost = ref(null)

const content = ref('')
// What was last loaded/saved — compared against `content` to know
// whether there's anything Save/a switch-away confirmation needs to
// care about.
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)

// The backend decides these (see db.Db.has_undo/has_redo, scoped to the
// current user), refreshed from its own response on every load/save/
// undo/redo — this component never tracks version numbers itself, it
// just asks for undo/redo and shows whatever content that yields.
const canUndo = ref(false)
const canRedo = ref(false)

// The same extension-based rule ProjectService._file_undo_redo_info
// derives this from (see its own docstring) — fixed for this file's
// whole lifetime (its extension, hence :key="fileName" on this
// component, never changes without a remount), so unlike can_undo/
// can_redo this only ever needs setting once, on load.
const mediaType = ref(null)

let view = null
const editableCompartment = new Compartment()

// Guards only *this* instance's own out-of-order async responses (a
// rapid double-click on Undo before the first one resolves) — not
// cross-file races, which :key="fileName" remounting already rules out
// on its own (see this component's own docstring).
let requestToken = 0

function createEditor(doc) {
  const extensions = [
    basicSetup,
    EditorView.lineWrapping,
    editableCompartment.of(EditorView.editable.of(true)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) content.value = update.state.doc.toString()
    }),
    // Ctrl-S (Cmd-S on Mac, via CodeMirror's own "Mod-" alias) — same
    // guard as the toolbar's Save button (:disabled), and always
    // swallows the key itself so the browser's native "Save page as"
    // never opens, even when there's nothing to save.
    keymap.of([
      {
        key: 'Mod-s',
        run: () => {
          if (!loading.value && !saving.value && isDirty.value) save()
          return true
        }
      }
    ])
  ]
  if (YAML_PATTERN.test(props.fileName)) extensions.splice(1, 0, yaml())
  view = new EditorView({ doc, extensions, parent: editorHost.value })
}

function destroyEditor() {
  view?.destroy()
  view = null
}

// Replaces the editor's whole document in place (undo/redo, and
// refreshing after a save) — `content` updates itself via the
// updateListener already wired in createEditor, callers never set it
// directly.
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
    content.value = file.content
    originalContent.value = file.content
    canUndo.value = file.can_undo
    canRedo.value = file.can_redo
    mediaType.value = file.media_type
  } catch {
    if (token === requestToken) loading.value = false
    return
  }
  loading.value = false
  // A later call (see reload()) with a view already mounted — replace its
  // buffer in place, same as save()/undo/redo already do, rather than
  // creating a second EditorView on top of the still-live first one
  // (createEditor below only ever runs once, on the very first load).
  if (view) {
    setEditorDoc(content.value)
    return
  }
  await nextTick() // editorHost is v-show'd by loading above — wait a tick for layout to settle
  if (token !== requestToken) return
  createEditor(content.value)
}

// Purely persistence, never navigation — the toolbar's own Save button
// calls this directly and stays open regardless of outcome. Returns
// whether it succeeded; on failure the shared error store already has
// the message (see api.js's apiFetch).
async function save() {
  saving.value = true
  try {
    const result = await putProjectFile(props.projectName, props.fileName, content.value)
    // Refresh from the server's own response (can_undo/can_redo, plus
    // content for consistency) rather than trusting what was typed.
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

// Reverts the buffer to whatever was last loaded/saved, discarding any
// local unsaved edit — EditProjectView.vue's own unsaved-changes dialog
// calls this (regardless of which kind of editor is actually active, see
// its own activeEditor() helper) when the user picks "Discard" rather
// than "Save" before an action that would otherwise clobber this file's
// content right back out from under them.
function discard() {
  content.value = originalContent.value
  setEditorDoc(originalContent.value)
}

// Undo/redo ask the backend to preview the previous/next content from
// the current user's own history (see api.js's undoProjectFile/
// redoProjectFile), sending the editor's own current content along so a
// later redo/undo can bring it back. Unlike save(), this is a pure
// editor preview: nothing is persisted, no 'saved' emitted.
// `originalContent` deliberately stays put, so the editor's content now
// differing from it is exactly what lights Save back up.
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

// Moves the cursor to a specific line (0-based) and focuses the editor —
// the caller's own job to find *which* line (see EditProjectView.vue's
// findStateLine/findActionLine/findSignalLine, index.yml-specific and so
// kept out of this otherwise file-agnostic component). Best-effort: a
// lineIndex past the document's own end is simply ignored.
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

// Re-fetches this file's own content from the backend and replaces the
// editor's live buffer with it — for a caller that just changed this
// exact file through some *other* path than this component's own save()
// (see IndexYmlEditorView.vue's own add/edit/delete/reorder handlers,
// all of which write index.yml directly through AutomatonYamlEditor).
// Only safe to call with no local unsaved changes of its own — the
// caller's own job to have checked isDirty first (see EditProjectView.
// vue's own unsaved-changes guard) and warned/blocked otherwise, since
// this always simply overwrites whatever's currently in the buffer.
async function reload() {
  await load()
}

defineExpose({ content, isDirty, canUndo, canRedo, mediaType, loading, saving, save, discard, undo, redo, jumpToLine, reload })

// Read-only for the duration of an in-flight save — typing over content
// that's already mid-flight to the backend would just be silently lost
// the moment that request's own response overwrites the buffer (see
// save()'s own setEditorDoc(result.content) call).
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
