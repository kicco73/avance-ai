<script setup>
// Topbar "⚙" menu: dropdown with toggle / click-outside-to-close,
// offering Manage projects and backup download/restore actions.
import { computed, onBeforeUnmount, ref } from 'vue'
import { roleSatisfies } from '../../roles.js'

const props = defineProps({
  // App.vue only renders this component once the current user is at
  // least 'supervisor' — this further disables the 'admin'-only items
  // for a plain supervisor, per each action's own backend role gate.
  role: { type: String, default: null }
})

const emit = defineEmits(['manage-projects', 'manage-users', 'label-sessions', 'download-backup', 'restore-backup', 'about'])

const open = ref(false)
const rootEl = ref(null)
const restoreInput = ref(null)

const canManageProjects = computed(() => roleSatisfies(props.role, 'admin'))
const canManageUsers = computed(() => roleSatisfies(props.role, 'admin'))
const canLabelSessions = computed(() => roleSatisfies(props.role, 'supervisor'))
const canBackup = computed(() => roleSatisfies(props.role, 'admin'))
const canViewAbout = computed(() => roleSatisfies(props.role, 'supervisor'))

function toggle() {
  open.value = !open.value
}

function selectManageProjects() {
  open.value = false
  emit('manage-projects')
}

function selectManageUsers() {
  open.value = false
  emit('manage-users')
}

function selectLabelSessions() {
  open.value = false
  emit('label-sessions')
}

function selectDownloadBackup() {
  open.value = false
  emit('download-backup')
}

function selectRestoreBackup() {
  open.value = false
  restoreInput.value?.click()
}

function selectAbout() {
  open.value = false
  emit('about')
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
          <button class="settings-item" :disabled="!canManageProjects" :title="canManageProjects ? '' : 'Requires admin access'" @click="selectManageProjects">Manage projects</button>
        </li>
        <li>
          <button class="settings-item" :disabled="!canManageUsers" :title="canManageUsers ? '' : 'Requires admin access'" @click="selectManageUsers">Manage users</button>
        </li>
        <li>
          <button class="settings-item" :disabled="!canLabelSessions" :title="canLabelSessions ? '' : 'Requires supervisor access'" @click="selectLabelSessions">Label sessions</button>
        </li>
        <li class="settings-separator" role="separator"></li>
        <li>
          <button class="settings-item" :disabled="!canBackup" :title="canBackup ? '' : 'Requires admin access'" @click="selectDownloadBackup">Download backup</button>
        </li>
        <li>
          <button class="settings-item" :disabled="!canBackup" :title="canBackup ? '' : 'Requires admin access'" @click="selectRestoreBackup">Restore backup...</button>
        </li>
        <li class="settings-separator" role="separator"></li>
        <li>
          <button class="settings-item" :disabled="!canViewAbout" @click="selectAbout">About Avance...</button>
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

/* Matches ChatWindow.vue's .sessions-reopen-btn exactly — both are
   overlay icon buttons on the main chat screen, semi-transparent until
   hovered rather than always fully opaque. */
.settings-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  opacity: 0.35;
  transition: opacity 0.15s ease;
}

.settings-btn:hover {
  opacity: 1;
}

.settings-panel {
  position: absolute;
  top: calc(100% + 0.4rem);
  left: 0;
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

.settings-item:hover:not(:disabled) {
  background: #f0f4fa;
}

.settings-item:disabled {
  color: #999;
  cursor: not-allowed;
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
