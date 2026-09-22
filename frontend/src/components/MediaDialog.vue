<script setup>
import { computed, onMounted, ref } from 'vue'
import VuePdfEmbed from 'vue-pdf-embed'
import { renderMarkdown } from '../markdown.js'
import { mediaKindFromUrl } from '../mediaKind.js'
import { getDriveFiles, getMe, postDownloadMediaToDrive, postSaveMediaToDrive } from '../api.js'
import { roleSatisfies } from '../roles.js'
import { notify } from '../toastStore.js'
import ProgressSpinner from './ProgressSpinner.vue'

const props = defineProps({
  url: { type: String, required: true }
})

const kind = computed(() => mediaKindFromUrl(props.url))
const markdownHtml = ref('')
const ready = ref(false)

function markReady() {
  ready.value = true
}

const PROJECT_FILE_CONTENT_PATTERN = /\/core\/projects\/([^/]+)\/files\/(.+)\/content(?:[?#]|$)/

const projectFile = computed(() => {
  const match = props.url.match(PROJECT_FILE_CONTENT_PATTERN)
  return match ? { projectId: match[1], fileName: match[2] } : null
})

const driveActionsLoaded = ref(false)
const canUseDrive = ref(false)
const alreadyInDrive = ref(false)
const saving = ref(false)
const downloading = ref(false)

const canSave = computed(() => kind.value === 'pdf' && driveActionsLoaded.value && canUseDrive.value && !alreadyInDrive.value)
const canDownload = computed(() => kind.value === 'pdf' && driveActionsLoaded.value && canUseDrive.value)

async function loadDriveActions() {
  if (kind.value !== 'pdf' || !projectFile.value) return
  const { projectId, fileName } = projectFile.value
  try {
    const [me, drive] = await Promise.all([getMe(), getDriveFiles(projectId)])
    canUseDrive.value = roleSatisfies(me?.role, 'customer')
    alreadyInDrive.value = drive.files.some((file) => file.path === fileName)
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
  if (!projectFile.value || downloading.value) return
  const { projectId, fileName } = projectFile.value
  downloading.value = true
  try {
    await postDownloadMediaToDrive(projectId, fileName)
    alreadyInDrive.value = true
    triggerBrowserDownload(fileName)
  } catch (err) {
    notify('Could not download', err.message)
  } finally {
    downloading.value = false
  }
}

onMounted(async () => {
  loadDriveActions()
  if (kind.value !== 'markdown') return
  try {
    const response = await fetch(props.url, { credentials: 'include' })
    markdownHtml.value = renderMarkdown(await response.text())
  } catch {
  } finally {
    markReady()
  }
})
</script>

<template>
  <div class="media-dialog-wrap">
    <div v-if="canSave || canDownload" class="media-dialog-actions">
      <button v-if="canSave" type="button" class="media-dialog-action-btn" :disabled="saving" @click="handleSave">Save</button>
      <button v-if="canDownload" type="button" class="media-dialog-action-btn" :disabled="downloading" @click="handleDownload">Download</button>
    </div>
    <div class="media-dialog" :class="{ 'media-dialog-loading': !ready }">
      <div v-if="!ready" class="media-dialog-loading-overlay">
        <ProgressSpinner class="media-dialog-spinner" />
      </div>
      <div class="media-dialog-content" :class="{ 'media-dialog-content-ready': ready }">
        <VuePdfEmbed v-if="kind === 'pdf'" :key="url" :source="url" @rendered="markReady" @rendering-failed="markReady" @loading-failed="markReady" />
        <div v-else-if="kind === 'markdown'" class="media-dialog-markdown" v-html="markdownHtml"></div>
        <img v-else :key="url" :src="url" class="media-dialog-image" @load="markReady" @error="markReady" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.media-dialog-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-bottom: 0.6rem; }
.media-dialog-action-btn { padding: 0.35rem 0.9rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.media-dialog-action-btn:hover:not(:disabled) { background: #eef2f9; }
.media-dialog-action-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.media-dialog { position: relative; max-height: 80vh; overflow: auto; }
.media-dialog-loading { min-height: 240px; }
.media-dialog-loading-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.media-dialog-spinner { width: 32px; height: 32px; color: #4a6fa5; }
.media-dialog-content { opacity: 0; transform: scale(0.96); }
.media-dialog-content-ready { opacity: 1; transform: none; transition: opacity 0.2s ease, transform 0.2s ease; }
.media-dialog-image { display: block; max-width: 100%; max-height: 80vh; margin: 0 auto; border-radius: 6px; }
.media-dialog-markdown { text-align: left; }
.media-dialog-markdown :deep(p) { margin: 0 0 0.8rem; }
.media-dialog-markdown :deep(p:last-child) { margin-bottom: 0; }
.media-dialog-markdown :deep(img) { max-width: 100%; border-radius: 6px; }
</style>
