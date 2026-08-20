<script setup>
// Topbar "⚙" menu: dropdown with toggle / click-outside-to-close,
// offering Manage projects and backup download/restore actions.
import { onBeforeUnmount, ref } from 'vue'

const emit = defineEmits(['manage-projects', 'download-backup', 'restore-backup'])

const open = ref(false)
const rootEl = ref(null)
const restoreInput = ref(null)

function toggle() {
  open.value = !open.value
}

function selectManageProjects() {
  open.value = false
  emit('manage-projects')
}

function selectDownloadBackup() {
  open.value = false
  emit('download-backup')
}

function selectRestoreBackup() {
  open.value = false
  restoreInput.value?.click()
}

function handleRestoreFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  emit('restore-backup', file)
}

function handleClickOutside(event) {
  if (open.value && rootEl.value && !rootEl.value.contains(event.target)) {
    open.value = false
  }
}

document.addEventListener('click', handleClickOutside, true)

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside, true)
})
</script>

<template>
  <div class="settings-menu" ref="rootEl">
    <button class="settings-btn" title="Settings" @click="toggle">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M19.14 12.94c.04-.31.06-.62.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.03 7.03 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.59.24-1.13.56-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.65 8.84a.5.5 0 0 0 .12.64l2.03 1.58c-.04.31-.07.63-.07.94s.02.63.06.94l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32c.14.24.42.32.66.22l2.39-.96c.5.38 1.04.7 1.63.94l.36 2.54c.05.24.26.42.51.42h3.84c.25 0 .46-.18.5-.42l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.24.1.52.02.66-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z" />
      </svg>
    </button>

    <div v-if="open" class="settings-panel">
      <ul class="settings-list">
        <li>
          <button class="settings-item" @click="selectManageProjects">Manage projects</button>
        </li>
        <li class="settings-separator" role="separator"></li>
        <li>
          <button class="settings-item" @click="selectDownloadBackup">Download backup</button>
        </li>
        <li>
          <button class="settings-item" @click="selectRestoreBackup">Restore backup...</button>
        </li>
      </ul>
    </div>

    <input
      ref="restoreInput"
      type="file"
      accept=".sqlite"
      class="restore-backup-input"
      @change="handleRestoreFileChange"
    />
  </div>
</template>

<style scoped>
.settings-menu {
  position: relative;
}

/* Padding matches ProjectsMenu.vue's .projects-btn (0.4rem top/bottom)
   so both buttons compute to the same height via inherited line-height,
   without hardcoding a pixel value. */
.settings-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.4rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.settings-btn:hover {
  background: #4a6fa5;
  color: white;
}

.settings-panel {
  position: absolute;
  top: calc(100% + 0.4rem);
  right: 0;
  min-width: 180px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
}

.settings-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.settings-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  color: #4a6fa5;
}

.settings-item:hover {
  background: #f0f4fa;
}

.settings-separator {
  height: 1px;
  margin: 0.3rem 0;
  background: #eee;
}

.restore-backup-input {
  display: none;
}
</style>
