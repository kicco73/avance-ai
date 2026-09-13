<script setup>
import { computed, ref, watch } from 'vue'
import CardMenu from '../../../../components/skillkit/CardMenu.vue'
import { getProjectFile } from '../../api.js'
import { projectFileTypes } from '../../../../projectFileTypes.js'

const props = defineProps({
  projectId: { type: String, required: true },
  fileName: { type: String, required: true },
  deleting: { type: Boolean, default: false },
  renaming: { type: Boolean, default: false }
})

const emit = defineEmits(['delete', 'rename'])

function basenameOf(name) {
  const idx = name.lastIndexOf('/')
  return idx === -1 ? name : name.slice(idx + 1)
}

const renameable = computed(() => props.fileName !== 'index.css' && props.fileName !== 'legal/terms.md')

const editingTitle = ref(false)
const editBasename = ref('')

watch(() => props.fileName, () => { editingTitle.value = false })

function startRename() {
  if (!renameable.value || props.deleting || props.renaming) return
  editBasename.value = basenameOf(props.fileName)
  editingTitle.value = true
}

function commitRename() {
  if (!editingTitle.value) return
  editingTitle.value = false
  const trimmed = editBasename.value.trim()
  if (!trimmed || trimmed === basenameOf(props.fileName)) return
  emit('rename', trimmed)
}

function cancelRename() {
  editingTitle.value = false
}

function handleTitleKeydown(event) {
  if (event.key === 'Enter') {
    event.preventDefault()
    event.target.blur()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    cancelRename()
  }
}

const fileSize = ref(null)
async function loadFileSize() {
  fileSize.value = null
  try {
    const info = await getProjectFile(props.projectId, props.fileName)
    fileSize.value = info.size
  } catch {
  }
}
watch(() => props.fileName, loadFileSize, { immediate: true })

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const fileType = computed(() => projectFileTypes.value.of(props.fileName).label)

function handleDelete() {
  emit('delete')
}
</script>

<template>
  <div class="inspector-detail-card inspector-file-card">
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span class="inspector-detail-badge inspector-detail-badge-file">File</span>
        <input
          v-if="editingTitle"
          v-model="editBasename"
          class="inspector-detail-title-input"
          @click.stop
          @blur="commitRename"
          @keydown="handleTitleKeydown"
        />
        <span
          v-else
          class="inspector-detail-title"
          :class="{ 'inspector-file-card-title-renameable': renameable }"
          :title="fileName"
          @click="startRename"
        >{{ renaming ? 'Renaming…' : basenameOf(fileName) }}</span>
        <CardMenu>
          <button
            type="button"
            class="card-menu-item-danger"
            :disabled="deleting"
            @click="handleDelete"
          >{{ deleting ? 'Deleting…' : 'Delete' }}</button>
        </CardMenu>
      </div>
    </div>
    <div class="inspector-detail-body">
      <p class="inspector-detail-field"><strong>Type:</strong> {{ fileType }}</p>
      <p class="inspector-detail-field"><strong>Size:</strong> {{ fileSize == null ? '…' : formatFileSize(fileSize) }}</p>
    </div>
  </div>
</template>

<style scoped>
.inspector-file-card { cursor: default; }
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-file { background: #37474f; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-file-card-title-renameable { cursor: pointer; }
.inspector-file-card-title-renameable:hover { text-decoration: underline; }
.inspector-detail-title-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-detail-title-input:hover, .inspector-detail-title-input:focus { border-color: #ccc; background: white; }
.inspector-detail-body { padding: 0.6rem 0.75rem; font-size: 0.8rem; color: #444; }
.inspector-detail-field { margin: 0 0 0.3rem; line-height: 1.4; }
.inspector-detail-field:last-child { margin-bottom: 0; }
</style>
