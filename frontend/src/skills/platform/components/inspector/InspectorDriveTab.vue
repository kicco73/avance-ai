<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { driveFileContentUrl, getDriveFiles } from '../../api.js'
import { busChannel } from '../../../../busChannel.js'
import { openMediaDialog } from '../../../../openMediaDialog.js'

const props = defineProps({
  projectId: { type: String, required: true }
})

const filesLoading = ref(false)
const files = ref([])

async function loadFiles() {
  filesLoading.value = true
  try {
    files.value = (await getDriveFiles(props.projectId)).files
  } catch {
    files.value = []
  } finally {
    filesLoading.value = false
  }
}

function openFile(file) {
  openMediaDialog(driveFileContentUrl(props.projectId, file.path))
}

function formatFileSize(bytes) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`
}

function formatUpdatedAt(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

async function refresh(active) {
  if (active) await loadFiles()
}

let unsubscribe = null

onMounted(() => {
  unsubscribe = busChannel.subscribe('output.drive', (frame) => {
    if (frame.project_id === props.projectId) loadFiles()
  })
})

onUnmounted(() => unsubscribe?.())

defineExpose({ loadFiles, refresh })
</script>

<template>
  <div class="inspector-drive-section">
    <p v-if="filesLoading" class="signals-status">Loading…</p>
    <p v-else-if="!files.length" class="signals-status">Nothing has been saved to the drive yet.</p>
    <ul v-else class="inspector-drive-list">
      <li v-for="file in files" :key="file.path">
        <button type="button" class="inspector-drive-item" @click="openFile(file)">
          <span class="inspector-drive-path">{{ file.path }}</span>
          <span class="inspector-drive-meta">{{ formatFileSize(file.size) }} · {{ formatUpdatedAt(file.updated_at) }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.inspector-drive-section { flex: 1; min-height: 0; overflow-y: auto; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-drive-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.4rem; }
.inspector-drive-item {
  width: 100%;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.6rem;
  padding: 0.55rem 0.7rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
  cursor: pointer;
  text-align: left;
}
.inspector-drive-item:hover { background: #f0f0f0; }
.inspector-drive-path {
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inspector-drive-meta { flex-shrink: 0; font-size: 0.72rem; color: #999; }
</style>
