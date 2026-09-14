import { computed, nextTick, ref } from 'vue'
import { getProjectFiles, putProjectFile, putProjectFileBinary, deleteProjectFile, renameProjectFile, postAddLegalTerms } from './api.js'
import { setApiError, clearApiError } from '../../errorStore.js'
import { confirmDialog, promptDialog, chooseDialog } from '../../dialogStore.js'
import { findActionLine, findAttachmentLine, findEnvKeyLine, findInitActionLine, findSignalLine, findStateLine } from '../../indexYmlLineFinder.js'
import { ensureProjectFileTypes, projectFileTypes } from '../../projectFileTypes.js'

const INDEX_CSS_SKELETON = `.chat-header {
}

.chat-body {
}

.chat-footer {
}
`

const LEGAL_TERMS_FILE_NAME = 'legal/terms.md'

export function useProjectFiles(projectId, emit) {
  const filesLoading = ref(true)
  const files = ref([])
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
  const themeAssetNames = computed(() => files.value.filter((name) => name.startsWith('aspect/')))
  const hasTheme = computed(() => files.value.includes('index.css'))
  const hasLegalTerms = computed(() => files.value.includes(LEGAL_TERMS_FILE_NAME))
  const activeEditorIsDirty = computed(() => {
    if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value?.isDirty ?? false
    if (currentFileName.value === 'index.css') return indexCssEditorRef.value?.isDirty ?? false
    if (currentFileIsMedia.value) return false
    if (currentFileIsMarkdown.value) return mdEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
  })

  function activeEditor() {
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
      files.value = (await getProjectFiles(projectId)).files
    } catch {
    } finally {
      filesLoading.value = false
    }
  }

  function switchFile(fileName) {
    currentFileName.value = fileName
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

  function selectFile(fileName) {
    if (fileName === currentFileName.value) return
    guardedAction(`switch to "${fileName}"`, () => switchFile(fileName))
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

  async function handleUploadFile(event) {
    const uploadedFiles = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!uploadedFiles.length) return

    await ensureProjectFileTypes()
    const fileTypes = projectFileTypes.value

    const invalidNames = uploadedFiles.filter((file) => !fileTypes.accepts(file.name)).map((file) => file.name)
    if (invalidNames.length) {
      setApiError(
        `Only ${fileTypes.uploadableDescription} files can be uploaded — ` +
        `${invalidNames.map((name) => `"${name}"`).join(', ')} ${invalidNames.length === 1 ? "isn't" : "aren't"}.`
      )
      return
    }
    const oversizedFiles = uploadedFiles.filter((file) => fileTypes.oversized(file))
    if (oversizedFiles.length) {
      setApiError(
        `${oversizedFiles.map((file) => `"${file.name}" (max ${fileTypes.uploadLimitLabel(file.name)})`).join(', ')} ` +
        `${oversizedFiles.length === 1 ? 'is' : 'are'} larger than the upload limit.`
      )
      return
    }

    uploading.value = true
    clearApiError()
    try {
      for (const file of uploadedFiles) {
        const targetName = fileTypes.canonicalUploadName(file.name)
        if (fileTypes.hasEditor(file.name)) {
          const text = await file.text()
          await putProjectFile(projectId, targetName, text)
        } else {
          await putProjectFileBinary(projectId, targetName, file)
        }
      }
      await loadFiles()
      const lastUploadedName = fileTypes.canonicalUploadName(uploadedFiles[uploadedFiles.length - 1].name)
      await selectFile(lastUploadedName)
    } catch {
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

  async function handleNewAspect() {
    if (files.value.includes('index.css')) return
    await createProjectFile('index.css', INDEX_CSS_SKELETON)
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
    const cascadeAssets = fileName === 'index.css' ? themeAssetNames.value : []
    if (projectFileTypes.value.hasEditor(fileName)) {
      const confirmMessage = cascadeAssets.length
        ? `Delete "index.css"? This also deletes the ${cascadeAssets.length} asset${cascadeAssets.length === 1 ? '' : 's'} it can reference: ${cascadeAssets.join(', ')}.\n\nThis cannot be undone.`
        : `Delete file "${fileName}"? This cannot be undone.`
      const ok = await confirmDialog({ title: 'Delete file', body: confirmMessage, okLabel: 'Delete', danger: true })
      if (!ok) return
    }
    deletingFile.value = fileName
    clearApiError()
    try {
      await deleteProjectFile(projectId, fileName)
      await loadFiles()
      if (fileName === currentFileName.value || cascadeAssets.includes(currentFileName.value)) {
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
      await loadFiles()
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
    currentFileIsMedia, currentFileIsMarkdown, isBehaviorNodeSelected, hasTheme,
    activeEditorIsDirty, activeEditor,
    loadFiles, switchFile, guardedAction, selectFile, jumpToDefinition,
    handleUploadFile, handleNewAttachment, handleNewAspect, handleNewLegal, handleDeleteFile, handleRenameFile,
    handleFileRenamedByHistory, handleFileSaved,
  }
}
