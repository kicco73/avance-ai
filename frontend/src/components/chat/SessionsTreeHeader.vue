<script setup>
import { ref } from 'vue'
import ProgressSpinner from '../ProgressSpinner.vue'

defineProps({
  collapsed: { type: Boolean, default: false },
  hideCollapseToggle: { type: Boolean, default: false },
  allowImport: { type: Boolean, default: false },
  importing: { type: Boolean, default: false },
  importProgress: { type: Number, default: null },
  showDeleteAllImported: { type: Boolean, default: false },
  deleteAllDisabled: { type: Boolean, default: false },
  deletingAllImported: { type: Boolean, default: false }
})

const emit = defineEmits(['import', 'delete-all-imported', 'update:collapsed'])

const importInput = ref(null)

function triggerImport() {
  importInput.value?.click()
}

function onImportFileChosen(event) {
  const files = Array.from(event.target.files ?? [])
  if (files.length) emit('import', files)
  event.target.value = ''
}
</script>

<template>
  <div class="sessions-tree-header">
    <span v-if="!collapsed" class="sessions-tree-title">Sessions</span>
    <div style="display: flex">
      <div v-if="!collapsed && (allowImport || showDeleteAllImported)" class="sessions-tree-header-actions">
        <button
          v-if="allowImport"
          type="button"
          class="sessions-tree-icon-btn"
          :class="{ 'sessions-tree-icon-btn-busy': importing }"
          :disabled="importing"
          :title="importing ? (importProgress != null ? `Importing… ${Math.round(importProgress)}%` : 'Importing…') : `Import transcript(s) — .txt or a 'Download all' .json export`"
          @click="triggerImport"
        >
          <svg v-if="!importing" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
          </svg>
          <ProgressSpinner v-else :progress="importProgress" />
        </button>
        <input v-if="allowImport" ref="importInput" type="file" accept=".txt,text/plain,.json,application/json" multiple class="sessions-tree-import-input" @change="onImportFileChosen" />
        <button
          v-if="showDeleteAllImported"
          type="button"
          class="sessions-tree-icon-btn sessions-tree-icon-btn-danger"
          :class="{ 'sessions-tree-icon-btn-busy': deletingAllImported }"
          :disabled="deleteAllDisabled || deletingAllImported"
          :title="deletingAllImported ? 'Deleting…' : 'Delete all imported sessions'"
          @click="emit('delete-all-imported')"
        >
          <svg v-if="!deletingAllImported" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M9 3h6l1 2h4v2H4V5h4l1-2zm-3 6h12l-1 12H7L6 9zm3 2v8h2v-8H9zm4 0v8h2v-8h-2z" />
          </svg>
          <ProgressSpinner v-else />
        </button>
      </div>
      <button
        v-if="!hideCollapseToggle"
        class="collapse-toggle-btn"
        :title="collapsed ? 'Expand sessions' : 'Collapse sessions'"
        @click="emit('update:collapsed', !collapsed)"
      >{{ collapsed ? '▸' : '◂' }}</button>
    </div>
  </div>
</template>

<style scoped>
.sessions-tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #ddd;
}

.sessions-tree-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.sessions-tree-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sessions-tree-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
}

.sessions-tree-icon-btn:hover {
  background: #4a6fa5;
  color: white;
}

.sessions-tree-icon-btn-busy,
.sessions-tree-icon-btn-busy:hover {
  cursor: default;
  background: white;
  color: #4a6fa5;
}

.sessions-tree-icon-btn-danger {
  border-color: #c62828;
  color: #c62828;
}

.sessions-tree-icon-btn-danger:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.sessions-tree-icon-btn-danger:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.collapse-toggle-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 0.9rem;
}

.collapse-toggle-btn:hover {
  background: #eee;
}

.sessions-tree-import-input {
  display: none;
}
</style>
