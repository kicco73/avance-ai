import { computed, nextTick, ref } from 'vue'
import { putProjectFile, putProjectFileBinary, deleteProjectFile, renameProjectFile, postAddLegalTerms } from './api.js'
import { setApiError, clearApiError } from '../../errorStore.js'
import { confirmDialog, promptDialog, chooseDialog } from '../../dialogStore.js'
import { findActionLine, findAttachmentLine, findEnvKeyLine, findInitActionLine, findSignalLine, findStateLine } from '../../indexYmlLineFinder.js'
import { ensureProjectFileTypes, projectFileTypes } from '../../projectFileTypes.js'
import { projectFiles, refreshProjectFiles } from './projectFiles.js'

const LEGAL_TERMS_FILE_NAME = 'legal/terms.md'

export function useProjectFiles(projectId, emit) {
  const filesLoading = ref(true)
  const files = projectFiles
  files.value = []
  const currentFileName = ref('index.yml')

  const uploading = ref(false)
  const creatingFile = ref(false)
  const deletingFile = ref(null)

  const designPanelRef = ref(null)
  const codeEditorRef = computed(() => designPanelRef.value?.codeEditorRef ?? null)
  const indexYmlEditorRef = computed(() => designPanelRef.value?.indexYmlEditorRef ?? null)
  const indexCssEditorRef = computed(() => designPanelRef.value?.indexCssEditorRef ?? null)
  const mdEditorRef = computed(() => designPanelRef.value?.mdEditorRef ?? null)
  const currentFileIsMedia = computed(() => {
    const name = currentFileName.value ?? ''
    return name !== '' && !projectFileTypes.value.hasEditor(name)
  })
  const currentFileIsMarkdown = computed(() => /\.(md|txt)$/i.test(currentFileName.value ?? ''))
  const isBehaviorNodeSelected = computed(() => currentFileName.value === 'index.yml')
  const hasLegalTerms = computed(() => files.value.includes(LEGAL_TERMS_FILE_NAME))
  const mediaRootSelected = ref(false)
  const attachmentsRootSelected = ref(false)
  const activeEditorIsDirty = computed(() => {
    if (mediaRootSelected.value || attachmentsRootSelected.value) return false
    if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value?.isDirty ?? false
    if (currentFileName.value === 'index.css') return indexCssEditorRef.value?.isDirty ?? false
    if (currentFileIsMedia.value) return false
    if (currentFileIsMarkdown.value) return mdEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
  })

  function activeEditor() {
    if (mediaRootSelected.value || attachmentsRootSelected.value) return null
    if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value
    if (currentFileName.value === 'index.css') return indexCssEditorRef.value
    if (currentFileIsMedia.value) return null
    if (currentFileIsMarkdown.value) return mdEditorRef.value
    return codeEditorRef.value
  }

  async function loadFiles() {
    filesLoading.value = true
    try {
      await ensureProjectFileTypes()
      await refreshProjectFiles(projectId)
    } catch {
    } finally {
      filesLoading.value = false
    }
  }

  function switchFile(fileName) {
    mediaRootSelected.value = false
    attachmentsRootSelected.value = false
    currentFileName.value = fileName
  }

  function selectMediaRoot() {
    guardedAction('view media', () => { mediaRootSelected.value = true; attachmentsRootSelected.value = false })
  }

  function selectAttachmentsRoot() {
    guardedAction('view attachments', () => { attachmentsRootSelected.value = true; mediaRootSelected.value = false })
  }

  function guardedAction(label, run) {
    if (!activeEditorIsDirty.value) {
      return run()
    }
    return runGuardedAction(label, run)
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
      if (await activeEditor()?.save?.()) return run()
      return undefined
    }
    if (choice === 'discard') {
      activeEditor()?.discard?.()
      return run()
    }
    pendingCursorTarget.value = null
    return undefined
  }

  async function ensureAspectFileExists() {
    if (files.value.includes('index.css')) return
    try {
      await putProjectFile(projectId, 'index.css', '')
      await loadFiles()
    } catch {
    }
  }

  function selectFile(fileName) {
    if (fileName === currentFileName.value) return
    return guardedAction(`switch to "${fileName}"`, async () => {
      if (fileName === 'index.css') await ensureAspectFileExists()
      switchFile(fileName)
    })
  }

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

  async function jumpToDefinition(target, { silent = false } = {}) {
    pendingCursorTarget.value = target
    if (currentFileName.value !== 'index.yml') {
      if (silent) {
        pendingCursorTarget.value = null
        return
      }
      await selectFile('index.yml')
      if (currentFileName.value !== 'index.yml') return
    }
    await nextTick()
    while (indexYmlEditorRef.value && !indexYmlEditorRef.value.content) {
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    applyPendingCursorTarget()
  }

  async function uploadFiles(uploadedFiles, { accepts, describeAcceptable, canonicalNameFor }) {
    const fileTypes = projectFileTypes.value
    const invalidNames = uploadedFiles.filter((file) => !accepts(file.name)).map((file) => file.name)
    if (invalidNames.length) {
      setApiError(
        `Only ${describeAcceptable} files can be uploaded — ` +
        `${invalidNames.map((name) => `"${name}"`).join(', ')} ${invalidNames.length === 1 ? "isn't" : "aren't"}.`
      )
      return null
    }
    const oversizedFiles = uploadedFiles.filter((file) => fileTypes.oversized(file))
    if (oversizedFiles.length) {
      setApiError(
        `${oversizedFiles.map((file) => `"${file.name}" (max ${fileTypes.uploadLimitLabel(file.name)})`).join(', ')} ` +
        `${oversizedFiles.length === 1 ? 'is' : 'are'} larger than the upload limit.`
      )
      return null
    }

    uploading.value = true
    clearApiError()
    let lastUploadedName = null
    try {
      for (const file of uploadedFiles) {
        const targetName = canonicalNameFor(file.name)
        if (fileTypes.hasEditor(file.name)) {
          const text = await file.text()
          await putProjectFile(projectId, targetName, text)
        } else {
          await putProjectFileBinary(projectId, targetName, file)
        }
        lastUploadedName = targetName
      }
      await loadFiles()
    } catch {
      return null
    } finally {
      uploading.value = false
    }
    return lastUploadedName
  }

  async function handleUploadFile(event) {
    const uploadedFiles = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!uploadedFiles.length) return
    await ensureProjectFileTypes()
    const fileTypes = projectFileTypes.value
    const lastUploadedName = await uploadFiles(uploadedFiles, {
      accepts: (name) => fileTypes.accepts(name),
      describeAcceptable: fileTypes.uploadableDescription,
      canonicalNameFor: (name) => fileTypes.canonicalUploadName(name),
    })
    if (lastUploadedName) await selectFile(lastUploadedName)
  }

  const ATTACHMENT_UPLOAD_RE = /\.(md|txt)$/i

  async function handleUploadAttachment(event) {
    const uploadedFiles = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!uploadedFiles.length) return
    await ensureProjectFileTypes()
    const fileTypes = projectFileTypes.value
    const lastUploadedName = await uploadFiles(uploadedFiles, {
      accepts: (name) => ATTACHMENT_UPLOAD_RE.test(name),
      describeAcceptable: 'Markdown or text',
      canonicalNameFor: (name) => fileTypes.canonicalUploadName(name),
    })
    if (lastUploadedName) await selectFile(lastUploadedName)
  }

  async function handleUploadMedia(event) {
    const uploadedFiles = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!uploadedFiles.length) return
    await ensureProjectFileTypes()
    const fileTypes = projectFileTypes.value
    const lastUploadedName = await uploadFiles(uploadedFiles, {
      accepts: (name) => fileTypes.acceptsMedia(name),
      describeAcceptable: fileTypes.mediaUploadableDescription,
      canonicalNameFor: (name) => fileTypes.canonicalMediaUploadName(name),
    })
    if (lastUploadedName) await selectFile(lastUploadedName)
  }

  function toMdFileName(base) {
    return `${base.replace(/\.md$/i, '')}.md`
  }

  async function createProjectFile(name, content) {
    creatingFile.value = true
    clearApiError()
    try {
      await putProjectFile(projectId, name, content)
      await loadFiles()
      await selectFile(name)
    } catch {
    } finally {
      creatingFile.value = false
    }
  }

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
    if (rawName === null) return
    await createProjectFile(`behaviour/${toMdFileName(rawName.trim())}`, '')
  }

  async function handleNewLegal() {
    if (hasLegalTerms.value) return
    creatingFile.value = true
    clearApiError()
    try {
      await postAddLegalTerms(projectId)
      await loadFiles()
      await selectFile(LEGAL_TERMS_FILE_NAME)
    } catch {
    } finally {
      creatingFile.value = false
    }
  }

  async function handleDeleteFile(fileName) {
    if (fileName === 'index.yml') return
    if (projectFileTypes.value.hasEditor(fileName)) {
      const confirmMessage = fileName === 'index.css'
        ? 'Delete "index.css"? This also deletes any media/ asset it still references.\n\nThis cannot be undone.'
        : `Delete file "${fileName}"? This cannot be undone.`
      const ok = await confirmDialog({ title: 'Delete file', body: confirmMessage, okLabel: 'Delete', danger: true })
      if (!ok) return
    }
    deletingFile.value = fileName
    clearApiError()
    try {
      await deleteProjectFile(projectId, fileName)
      await loadFiles()
      if (!files.value.includes(currentFileName.value)) {
        await switchFile('index.yml')
      }
    } catch {
    } finally {
      deletingFile.value = null
    }
  }

  function basenameOf(name) {
    const idx = name.lastIndexOf('/')
    return idx === -1 ? name : name.slice(idx + 1)
  }

  const renamingFile = ref(null)

  async function handleRenameFile(fileName, newBasename) {
    const trimmed = newBasename.trim()
    if (!trimmed || trimmed === basenameOf(fileName)) return
    renamingFile.value = fileName
    clearApiError()
    try {
      const result = await renameProjectFile(projectId, fileName, trimmed)
      if (fileName === currentFileName.value) {
        switchFile(result.new_name)
      } else {
        if (currentFileName.value === 'index.yml') await indexYmlEditorRef.value?.reload?.()
        if (currentFileName.value === 'index.css') await indexCssEditorRef.value?.reload?.()
      }
    } catch {
    } finally {
      renamingFile.value = null
    }
  }

  async function handleFileRenamedByHistory(newName) {
    await loadFiles()
    switchFile(newName)
  }

  function handleFileSaved() {
    emit('saved')
  }

  return {
    filesLoading, files, currentFileName, uploading, creatingFile, deletingFile, renamingFile,
    designPanelRef, codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef,
    currentFileIsMedia, currentFileIsMarkdown, isBehaviorNodeSelected,
    mediaRootSelected, selectMediaRoot,
    attachmentsRootSelected, selectAttachmentsRoot,
    activeEditorIsDirty, activeEditor,
    loadFiles, switchFile, guardedAction, selectFile, jumpToDefinition,
    handleUploadFile, handleUploadMedia, handleUploadAttachment, handleNewAttachment, handleNewLegal, handleDeleteFile, handleRenameFile,
    handleFileRenamedByHistory, handleFileSaved,
  }
}
