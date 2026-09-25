<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import VuePdfEmbed from 'vue-pdf-embed'
import { renderMarkdown } from '../markdown.js'
import { getDriveFiles, getMe, postDownloadMediaToDrive, postRecordDriveDownload, postSaveMediaToDrive } from '../api.js'
import { roleSatisfies } from '../roles.js'
import { notify } from '../toastStore.js'
import ProgressSpinner from './ProgressSpinner.vue'

const props = defineProps({
  url: { type: String, required: true }
})

const closeDialog = inject('closeDialog')
const setDialogPending = inject('setDialogPending', () => {})

const kind = ref(null)
const pdfData = ref(null)
const markdownHtml = ref('')
const blobUrl = ref(null)
const ready = ref(false)
const failed = ref(false)

watch(ready, (isReady) => setDialogPending(!isReady), { immediate: true })

function markReady() {
  ready.value = true
}

function markFailed() {
  failed.value = true
  markReady()
}

function kindOfContentType(contentType) {
  if (contentType === 'application/pdf') return 'pdf'
  if (contentType.startsWith('text/')) return 'markdown'
  if (contentType.startsWith('audio/')) return 'audio'
  return 'image'
}

async function loadContent() {
  try {
    const response = await fetch(props.url, { credentials: 'include' })
    if (!response.ok) {
      markFailed()
      return
    }
    const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
    const resolvedKind = kindOfContentType(contentType)
    if (resolvedKind === 'pdf') {
      const bytes = new Uint8Array(await response.arrayBuffer())
      pdfData.value = bytes
      kind.value = 'pdf'
      return
    }
    if (resolvedKind === 'markdown') {
      const html = renderMarkdown(await response.text())
      markdownHtml.value = html
      kind.value = 'markdown'
      markReady()
      return
    }
    const objectUrl = URL.createObjectURL(await response.blob())
    blobUrl.value = objectUrl
    kind.value = resolvedKind
    if (kind.value !== 'image') markReady()
  } catch {
    markFailed()
  }
}

const PROJECT_FILE_CONTENT_PATTERN = /\/core\/projects\/([^/]+)\/files\/(.+)\/content(?:[?#]|$)/
const DRIVE_FILE_CONTENT_PATTERN = /\/core\/projects\/([^/]+)\/drive\/(.+?)(?:[?#]|$)/

const projectFile = computed(() => {
  const match = props.url.match(PROJECT_FILE_CONTENT_PATTERN)
  return match ? { projectId: match[1], fileName: match[2] } : null
})

const driveFile = computed(() => {
  if (projectFile.value) return null
  const match = props.url.match(DRIVE_FILE_CONTENT_PATTERN)
  return match ? { projectId: decodeURIComponent(match[1]), path: decodeURIComponent(match[2]) } : null
})

const driveActionsLoaded = ref(false)
const canUseDrive = ref(false)
const alreadyInDrive = ref(false)
const saving = ref(false)
const downloading = ref(false)

const canSave = computed(() => kind.value === 'pdf' && projectFile.value != null && driveActionsLoaded.value && canUseDrive.value && !alreadyInDrive.value)
const canDownload = computed(() => kind.value === 'pdf' && driveActionsLoaded.value && canUseDrive.value && (projectFile.value != null || driveFile.value != null))

async function loadDriveActions() {
  if (!projectFile.value && !driveFile.value) return
  try {
    const me = await getMe()
    canUseDrive.value = roleSatisfies(me?.role, 'customer')
    if (projectFile.value) {
      const { projectId, fileName } = projectFile.value
      const drive = await getDriveFiles(projectId)
      alreadyInDrive.value = drive.files.some((file) => file.path === fileName)
    }
  } catch {
  } finally {
    driveActionsLoaded.value = true
  }
}

async function handleSave() {
  if (!projectFile.value || saving.value) return
  const { projectId, fileName } = projectFile.value
  saving.value = true
  try {
    await postSaveMediaToDrive(projectId, fileName)
    alreadyInDrive.value = true
    notify('Saved', 'Added to your drive.')
  } catch (err) {
    notify('Could not save', err.message)
  } finally {
    saving.value = false
  }
}

function triggerBrowserDownload(fileName) {
  const link = document.createElement('a')
  link.href = props.url
  link.download = fileName.split('/').pop()
  link.click()
}

async function handleDownload() {
  if (downloading.value) return
  downloading.value = true
  try {
    if (projectFile.value) {
      const { projectId, fileName } = projectFile.value
      await postDownloadMediaToDrive(projectId, fileName)
      alreadyInDrive.value = true
      triggerBrowserDownload(fileName)
    } else if (driveFile.value) {
      const { projectId, path } = driveFile.value
      await postRecordDriveDownload(projectId, path)
      triggerBrowserDownload(path)
    }
  } catch (err) {
    notify('Could not download', err.message)
  } finally {
    downloading.value = false
  }
}

onMounted(() => {
  loadDriveActions()
  loadContent()
})

onBeforeUnmount(() => {
  if (blobUrl.value) URL.revokeObjectURL(blobUrl.value)
})
</script>

<template>
  <div class="media-dialog-wrap">
    <div class="media-dialog" :class="{ 'media-dialog-loading': !ready }">
      <div v-if="!ready" class="media-dialog-loading-overlay">
        <ProgressSpinner class="media-dialog-spinner" />
      </div>
      <p v-if="failed" class="media-dialog-status">Couldn't load this file.</p>
      <div v-else class="media-dialog-content" :class="{ 'media-dialog-content-ready': ready }">
        <VuePdfEmbed v-if="kind === 'pdf'" :key="url" :source="pdfData" @rendered="markReady" @rendering-failed="markFailed" @loading-failed="markFailed" />
        <div v-else-if="kind === 'markdown'" class="media-dialog-markdown markdown-content" v-html="markdownHtml"></div>
        <audio v-else-if="kind === 'audio'" :key="url" :src="blobUrl" controls class="media-dialog-audio"></audio>
        <img v-else-if="kind === 'image'" :key="url" :src="blobUrl" class="media-dialog-image" @load="markReady" @error="markFailed" />
      </div>
    </div>
    <div v-show="ready" class="media-dialog-actions">
      <button v-if="canSave" type="button" class="media-dialog-action-btn" :disabled="saving" @click="handleSave">Save</button>
      <button v-if="canDownload" type="button" class="media-dialog-action-btn" :disabled="downloading" @click="handleDownload">Download</button>
      <button type="button" class="media-dialog-action-btn media-dialog-close-btn" @click="closeDialog()">Close</button>
    </div>
  </div>
</template>

<style scoped>
.media-dialog-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.6rem; }
.media-dialog-action-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #4a6fa5; background: #4a6fa5; color: white; font-size: 0.85rem; cursor: pointer; }
.media-dialog-action-btn:hover:not(:disabled) { background: #3d5c8a; }
.media-dialog-action-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.media-dialog-close-btn { border-color: #ccc; background: white; color: #444; }
.media-dialog-close-btn:hover { background: #f0f0f0; }
.media-dialog { position: relative; max-height: 80vh; overflow: auto; }
.media-dialog-loading { min-height: 240px; }
.media-dialog-loading-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.media-dialog-spinner { width: 32px; height: 32px; color: #4a6fa5; }
.media-dialog-status { margin: 0; padding: 1rem 0; color: #444; text-align: center; }
.media-dialog-content { opacity: 0; transform: scale(0.96); }
.media-dialog-content-ready { opacity: 1; transform: none; transition: opacity 0.2s ease, transform 0.2s ease; }
.media-dialog-image { display: block; max-width: 100%; max-height: 80vh; margin: 0 auto; border-radius: 6px; }
.media-dialog-audio { display: block; width: 100%; margin: 1rem 0; }
/* .media-dialog-markdown itself carries no styles of its own any more —
   see src/styles/markdownContent.css's .markdown-content, applied
   alongside it in the template; the class stays only as a test hook. */
</style>
