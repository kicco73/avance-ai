<script setup>
// Design mode's file list — upload/new/delete/select. Purely presentational: state
// comes in as props, user actions are emitted back up to the parent's own handlers
// (handleUploadFile, handleNewFile, handleDeleteFile, selectFile).
import { ref } from 'vue'

defineProps({
  files: { type: Array, default: () => [] },
  filesLoading: { type: Boolean, default: true },
  currentFileName: { type: String, default: null },
  uploading: { type: Boolean, default: false },
  creatingFile: { type: Boolean, default: false },
  deletingFile: { type: String, default: null },
  explorerWidth: { type: Number, required: true }
})

const emit = defineEmits(['new-file', 'delete-file', 'select-file', 'upload-file'])

const fileInputRef = ref(null)

// Purely local: no parent state is involved until a file is actually chosen
// (see the input's own @change, which emits upload-file to the parent).
function triggerUpload() {
  fileInputRef.value?.click()
}
</script>

<template>
  <div class="file-explorer" :style="{ width: explorerWidth + 'px' }">
    <div class="file-explorer-header">
      <span class="file-explorer-title">Files</span>
      <div class="file-explorer-header-actions">
        <button class="file-explorer-icon-btn" :disabled="uploading" title="Upload file" @click="triggerUpload">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
          </svg>
        </button>
        <button class="file-explorer-icon-btn" :disabled="creatingFile" title="New file" @click="emit('new-file')">+</button>
      </div>
      <input
        ref="fileInputRef"
        type="file"
        accept=".txt,.yml,.yaml,.css,.png,.jpg,.jpeg,.gif,.webp,.svg"
        class="file-explorer-upload-input"
        @change="emit('upload-file', $event)"
      />
    </div>
    <p v-if="filesLoading" class="file-explorer-status">Loading…</p>
    <ul v-else class="file-explorer-list">
      <li v-for="name in files" :key="name" class="file-explorer-row">
        <button
          class="file-explorer-item"
          :class="{ 'file-explorer-item-active': name === currentFileName }"
          :title="name"
          @click="emit('select-file', name)"
        >
          {{ name }}
        </button>
        <button
          v-if="name !== 'index.yml'"
          class="file-explorer-delete-btn"
          :disabled="deletingFile === name"
          title="Delete file"
          @click="emit('delete-file', name)"
        >
          ×
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.file-explorer { flex-shrink: 0; display: flex; flex-direction: column; min-width: 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
.file-explorer-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.4rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #ddd; background: #f7f8fa; }
.file-explorer-header-actions { display: flex; gap: 0.4rem; }
.file-explorer-title { font-size: 0.8rem; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
.file-explorer-icon-btn { display: flex; align-items: center; justify-content: center; width: 1.6rem; height: 1.6rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; padding: 0; font-size: 0.9rem; line-height: 1; }
.file-explorer-icon-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.file-explorer-icon-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.file-explorer-upload-input { display: none; }
.file-explorer-status { margin: 0; padding: 0.6rem; font-size: 0.85rem; color: #444; }
.file-explorer-list { list-style: none; margin: 0; padding: 0.3rem; overflow-y: auto; flex: 1; }
.file-explorer-row { display: flex; align-items: center; gap: 0.2rem; }
.file-explorer-item { flex: 1; min-width: 0; display: block; text-align: left; padding: 0.4rem 0.5rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.85rem; color: #333; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.file-explorer-item:hover { background: #f0f4fa; }
.file-explorer-item-active { background: #e4ecf9; color: #2c4d7a; font-weight: 600; }
.file-explorer-delete-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #c62828; cursor: pointer; font-size: 1rem; }
.file-explorer-delete-btn:hover:not(:disabled) { background: #fdecea; }
.file-explorer-delete-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
