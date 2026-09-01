import { computed, nextTick, ref } from 'vue'
import { getProjectFiles, putProjectFile, putProjectFileBinary, deleteProjectFile, postAddLegalTerms } from '../api.js'
import { setApiError, clearApiError } from '../errorStore.js'
import { confirmDialog, promptDialog, chooseDialog } from '../dialogStore.js'
import { findActionLine, findAttachmentLine, findEnvKeyLine, findInitActionLine, findSignalLine, findStateLine } from '../indexYmlLineFinder.js'

// Upload (handleUploadFile below, and the file explorer's own hidden
// <input accept>) additionally allows every image extension the backend
// whitelists (see project_service.py's IMAGE_EXTENSIONS).
const IMAGE_PATTERN = /\.(png|jpe?g|gif|webp|svg)$/i
const UPLOADABLE_PATTERN = /\.(txt|md|csv|ya?ml|css|png|jpe?g|gif|webp|svg)$/i

function canonicalUploadName(fileName) {
  if (fileName === 'index.yml' || fileName === 'index.css') return fileName
  if (IMAGE_PATTERN.test(fileName) || /\.css$/i.test(fileName)) return `aspect/${fileName}`
  return `behaviour/${fileName}`
}
// Mirrors project_service.py's own MAX_IMAGE_UPLOAD_BYTES — checked here
// purely for immediate feedback; the backend enforces this authoritatively
// regardless.
const MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024

// "New aspect" seeds index.css with its three customizable regions,
// left empty — matches what both sample projects' own index.css style.
const INDEX_CSS_SKELETON = `.chat-header {
}

.chat-body {
}

.chat-footer {
}
`

// The one file allowed to live in a subfolder — see editor.py's
// LEGAL_TERMS_FILE_NAME. "New legal" (handleNewLegal below) is the only
// way to create it; the file explorer shows it under its own "Legal"
// branch instead of grouping it with the Behavior attachments.
const LEGAL_TERMS_FILE_NAME = 'legal/terms.md'

// EditProjectView.vue's file explorer and its currently-open editor:
// the file list, upload/create/delete actions, which file is open, the
// unsaved-changes guard around switching files, and jump-to-definition
// cursor placement. `emit` is the component's own defineEmits — only
// 'saved' is ever raised here (handleFileSaved).
export function useProjectFiles(projectName, emit) {
  const filesLoading = ref(true)
  const files = ref([])
  const currentFileName = ref('index.yml')
  // Name of the file a create/upload/new-legal flow just opened, so
  // ProjectDesignPanel.vue can default MdEditorPanel to its Edit tab
  // instead of Preview for it specifically — see switchFile's own
  // self-clearing logic below.
  const justAddedFileName = ref(null)

  const uploading = ref(false)
  const creatingFile = ref(false)
  const deletingFile = ref(null)

  // designPanelRef is this view's handle onto ProjectDesignPanel;
  // codeEditorRef/indexYmlEditorRef/indexCssEditorRef are computed proxies
  // through to the refs it exposes via defineExpose (which auto-unwraps).
  const designPanelRef = ref(null)
  // Whichever one is actually mounted (see ProjectDesignPanel.vue's
  // v-if/v-else, keyed off currentFileName === 'index.yml') — each owns
  // its own loading/saving/isDirty state internally (see activeEditorIsDirty below).
  const codeEditorRef = computed(() => designPanelRef.value?.codeEditorRef ?? null)
  const indexYmlEditorRef = computed(() => designPanelRef.value?.indexYmlEditorRef ?? null)
  const indexCssEditorRef = computed(() => designPanelRef.value?.indexCssEditorRef ?? null)
  const mdEditorRef = computed(() => designPanelRef.value?.mdEditorRef ?? null)
  // An image has no editor at all (see the file explorer's own <img>
  // preview branch below) — never dirty, nothing for activeEditor() to
  // save/discard.
  const currentFileIsImage = computed(() => IMAGE_PATTERN.test(currentFileName.value ?? ''))
  // A .txt/.md attachment gets MdEditorPanel's Preview/Edit toggle instead
  // of the bare CodeEditor fallback (see ProjectDesignPanel.vue).
  const currentFileIsMarkdown = computed(() => /\.(md|txt)$/i.test(currentFileName.value ?? ''))
  // Whether the file explorer's "Behavior" node itself (index.yml) is the
  // open file — as opposed to one of its attachments or anything under
  // "Theme". Only then does the Inspector have states/actions/signals to
  // show at all (see inspectorTabs and InspectorStateTab's own gating).
  const isBehaviorNodeSelected = computed(() => currentFileName.value === 'index.yml')
  // The file explorer's "Theme" branch children — every image asset index.css's
  // own url(...) rules could reference. Deleting index.css takes these down
  // with it (see handleDeleteFile) since an asset with no stylesheet left to
  // reference it is just dead weight.
  const themeAssetNames = computed(() => files.value.filter((name) => name.startsWith('aspect/')))
  // RunChat.vue's "Apply aspect" toggle only makes sense once a theme
  // actually exists to apply.
  const hasTheme = computed(() => files.value.includes('index.css'))
  // Gates the "New legal" menu item — only offered while no legal/terms.md
  // exists yet, and drives the file explorer's "Legal" branch visibility.
  const hasLegalTerms = computed(() => files.value.includes(LEGAL_TERMS_FILE_NAME))
  const activeEditorIsDirty = computed(() => {
    if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value?.isDirty ?? false
    if (currentFileName.value === 'index.css') return indexCssEditorRef.value?.isDirty ?? false
    if (currentFileIsImage.value) return false
    if (currentFileIsMarkdown.value) return mdEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
  })

  function activeEditor() {
    if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value
    if (currentFileName.value === 'index.css') return indexCssEditorRef.value
    if (currentFileIsImage.value) return null
    if (currentFileIsMarkdown.value) return mdEditorRef.value
    return codeEditorRef.value
  }

  async function loadFiles() {
    filesLoading.value = true
    try {
      files.value = (await getProjectFiles(projectName)).files
    } catch {
      // already surfaced via apiFetch
    } finally {
      filesLoading.value = false
    }
  }

  function switchFile(fileName) {
    currentFileName.value = fileName
    // selectedGraphElement is deliberately left alone here — the Inspector's
    // "State"/"Actions" selection stays valid while browsing another file.
    // justAddedFileName is a one-shot signal (see createProjectFile/
    // handleUploadFile/handleNewLegal below) — self-clearing the moment
    // navigation lands anywhere else, so returning to this same file
    // later doesn't keep defaulting to Edit.
    if (fileName !== justAddedFileName.value) justAddedFileName.value = null
  }

  // Every entry point that would discard unsaved code routes through here
  // instead of running `run` directly: dirty means ask first (via
  // runGuardedAction's chooseDialog), clean runs immediately.
  function guardedAction(label, run) {
    if (!activeEditorIsDirty.value) {
      run()
      return
    }
    runGuardedAction(label, run)
  }

  const pendingCursorTarget = ref(null)

  async function runGuardedAction(label, run) {
    const choice = await chooseDialog({
      title: 'Unsaved changes',
      body: `"${currentFileName.value}" has unsaved changes. Save before you ${label}?`,
      options: [
        { id: 'save', label: 'Save' },
        { id: 'discard', label: 'Discard' }
      ]
    })
    if (choice === 'save') {
      if (await activeEditor()?.save?.()) run()
      return
    }
    if (choice === 'discard') {
      // The whole point of "Discard": the active editor's dirty buffer
      // actually reverts to its last-loaded content.
      activeEditor()?.discard?.()
      run()
      return
    }
    // null (Cancel/backdrop/ESC) — a cursor jump that triggered this action
    // is moot once it's declined, so it shouldn't fire on some later,
    // unrelated action either.
    pendingCursorTarget.value = null
  }

  // Entry point for both explorer clicks and post-upload auto-open.
  function selectFile(fileName) {
    if (fileName === currentFileName.value) return
    guardedAction(`switch to "${fileName}"`, () => switchFile(fileName))
  }

  // Moves the editor's cursor to a definition clicked in the Inspect panel
  // (see jumpToDefinition). Best-effort: a target that findStateLine/
  // findActionLine/findSignalLine can't locate just leaves the cursor as-is.
  function applyPendingCursorTarget() {
    if (!pendingCursorTarget.value) return
    const text = indexYmlEditorRef.value?.content
    if (!text) return
    const target = pendingCursorTarget.value
    pendingCursorTarget.value = null
    const lines = text.split('\n')
    let lineIndex = null
    if (target.kind === 'state') lineIndex = findStateLine(lines, target.stateKey)
    else if (target.kind === 'action') {
      lineIndex = target.stateKey === '' ? findInitActionLine(lines) : findActionLine(lines, target.stateKey, target.actionName)
    } else if (target.kind === 'signal') lineIndex = findSignalLine(lines, target.signalName)
    else if (target.kind === 'env-key') lineIndex = findEnvKeyLine(lines, target.envKeyName)
    else if (target.kind === 'attachment') lineIndex = findAttachmentLine(lines, target.stateKey, target.fileName)
    if (lineIndex === null) return
    indexYmlEditorRef.value?.jumpToLine(lineIndex)
  }

  // Switches to index.yml first if it isn't already open (the only file
  // definitions live in). `silent` suppresses that switch, so a plain row
  // selection doesn't yank the user out of whatever file they're viewing.
  async function jumpToDefinition(target, { silent = false } = {}) {
    pendingCursorTarget.value = target
    if (currentFileName.value !== 'index.yml') {
      if (silent) {
        pendingCursorTarget.value = null
        return
      }
      await selectFile('index.yml')
      if (currentFileName.value !== 'index.yml') return // blocked by the unsaved-changes dialog
    }
    await nextTick()
    while (indexYmlEditorRef.value && !indexYmlEditorRef.value.content) {
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    applyPendingCursorTarget()
  }

  async function handleUploadFile(event) {
    const uploadedFiles = Array.from(event.target.files ?? [])
    event.target.value = '' // reset so re-selecting the same file(s) re-fires change
    if (!uploadedFiles.length) return

    const invalidNames = uploadedFiles.filter((file) => !UPLOADABLE_PATTERN.test(file.name)).map((file) => file.name)
    if (invalidNames.length) {
      setApiError(
        `Only .txt, .yml/.yaml, .css, or image (.png/.jpg/.gif/.webp/.svg) files can be uploaded — ` +
        `${invalidNames.map((name) => `"${name}"`).join(', ')} ${invalidNames.length === 1 ? "isn't" : "aren't"}.`
      )
      return
    }
    const oversizedNames = uploadedFiles
      .filter((file) => IMAGE_PATTERN.test(file.name) && file.size > MAX_IMAGE_UPLOAD_BYTES)
      .map((file) => file.name)
    if (oversizedNames.length) {
      setApiError(
        `${oversizedNames.map((name) => `"${name}"`).join(', ')} ` +
        `${oversizedNames.length === 1 ? 'is' : 'are'} larger than the 5 MB upload limit.`
      )
      return
    }

    uploading.value = true
    clearApiError()
    try {
      for (const file of uploadedFiles) {
        const targetName = canonicalUploadName(file.name)
        if (IMAGE_PATTERN.test(file.name)) {
          await putProjectFileBinary(projectName, targetName, file)
        } else {
          const text = await file.text()
          await putProjectFile(projectName, targetName, text)
        }
      }
      await loadFiles()
      const lastUploadedName = canonicalUploadName(uploadedFiles[uploadedFiles.length - 1].name)
      justAddedFileName.value = lastUploadedName
      await selectFile(lastUploadedName)
    } catch {
      // already surfaced via apiFetch
    } finally {
      uploading.value = false
    }
  }

  function toMdFileName(base) {
    return `${base.replace(/\.md$/i, '')}.md`
  }

  async function createProjectFile(name, content) {
    creatingFile.value = true
    clearApiError()
    try {
      await putProjectFile(projectName, name, content)
      await loadFiles()
      justAddedFileName.value = name
      await selectFile(name)
    } catch {
      // already surfaced via apiFetch
    } finally {
      creatingFile.value = false
    }
  }

  // validate runs inline as the user types, so the existence error shows
  // right under the field instead of bouncing off setApiError after the
  // prompt's already closed.
  async function handleNewAttachment() {
    const rawName = await promptDialog({
      title: 'New attachment',
      body: 'Attachment name (always saved as .md):',
      placeholder: 'notes',
      validate(value) {
        const trimmed = value.trim()
        if (!trimmed) return 'Enter a file name.'
        if (trimmed.includes('/')) return 'File names can\'t contain "/".'
        if (files.value.includes(`behaviour/${toMdFileName(trimmed)}`)) return `A file named "${toMdFileName(trimmed)}" already exists.`
        return null
      }
    })
    if (rawName === null) return // cancelled
    await createProjectFile(`behaviour/${toMdFileName(rawName.trim())}`, '')
  }

  async function handleNewAspect() {
    if (files.value.includes('index.css')) return
    await createProjectFile('index.css', INDEX_CSS_SKELETON)
  }

  async function handleNewLegal() {
    if (hasLegalTerms.value) return
    creatingFile.value = true
    clearApiError()
    try {
      await postAddLegalTerms(projectName)
      await loadFiles()
      justAddedFileName.value = LEGAL_TERMS_FILE_NAME
      await selectFile(LEGAL_TERMS_FILE_NAME)
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
    // The asset list here is only for the confirm prompt's own wording —
    // the cascade itself (deleting every asset along with index.css) is
    // server-side, see ProjectService.delete_project_file.
    const cascadeAssets = fileName === 'index.css' ? themeAssetNames.value : []
    // A lone Theme asset is a single, cheap, easily re-uploaded file with
    // nothing cascading from it — index.css (which does cascade) and every
    // other file still confirm.
    if (!IMAGE_PATTERN.test(fileName)) {
      const confirmMessage = cascadeAssets.length
        ? `Delete "index.css"? This also deletes the ${cascadeAssets.length} asset${cascadeAssets.length === 1 ? '' : 's'} it can reference: ${cascadeAssets.join(', ')}.\n\nThis cannot be undone.`
        : `Delete file "${fileName}"? This cannot be undone.`
      const ok = await confirmDialog({ title: 'Delete file', body: confirmMessage, okLabel: 'Delete', danger: true })
      if (!ok) return
    }
    deletingFile.value = fileName
    clearApiError()
    try {
      await deleteProjectFile(projectName, fileName)
      await loadFiles()
      if (fileName === currentFileName.value || cascadeAssets.includes(currentFileName.value)) {
        await switchFile('index.yml')
      }
    } catch {
      // already surfaced via apiFetch
    } finally {
      deletingFile.value = null
    }
  }

  function handleFileSaved() {
    emit('saved')
  }

  return {
    filesLoading, files, currentFileName, justAddedFileName, uploading, creatingFile, deletingFile,
    designPanelRef, codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef,
    currentFileIsImage, currentFileIsMarkdown, isBehaviorNodeSelected, hasTheme,
    activeEditorIsDirty, activeEditor,
    loadFiles, switchFile, guardedAction, selectFile, jumpToDefinition,
    handleUploadFile, handleNewAttachment, handleNewAspect, handleNewLegal, handleDeleteFile, handleFileSaved,
  }
}
