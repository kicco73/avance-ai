<script setup>
// Topbar "⚙" menu: dropdown with toggle / click-outside-to-close,
// offering Manage projects and Manage services.
import { computed, onBeforeUnmount, ref } from 'vue'
import { roleSatisfies } from '../../roles.js'

const props = defineProps({
  // App.vue only renders this component once the current user is at
  // least 'supervisor' — this further disables the 'admin'-only items
  // for a plain supervisor, per each action's own backend role gate.
  role: { type: String, default: null }
})

const emit = defineEmits([
  'manage-users', 'manage-services', 'app-store'
])

const open = ref(false)
const rootEl = ref(null)

const canManageUsers = computed(() => roleSatisfies(props.role, 'admin'))
const canManageServices = computed(() => roleSatisfies(props.role, 'admin'))

function toggle() {
  open.value = !open.value
}

function selectManageUsers() {
  open.value = false
  emit('manage-users')
}

function selectManageServices() {
  open.value = false
  emit('manage-services')
}

function selectAppStore() {
  open.value = false
  emit('app-store')
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

    <Transition name="settings-panel">
      <div v-if="open" class="settings-panel">
        <ul class="settings-list">
          <li>
            <button class="settings-item" :disabled="!canManageUsers" :title="canManageUsers ? '' : 'Requires admin access'" @click="selectManageUsers">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" />
              </svg>
              <span>Manage users</span>
            </button>
          </li>
          <li class="settings-separator" role="separator"></li>
          <li>
            <button class="settings-item" :disabled="!canManageServices" :title="canManageServices ? '' : 'Requires admin access'" @click="selectManageServices">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z" />
              </svg>
              <span>Manage services</span>
            </button>
          </li>
          <li class="settings-separator" role="separator"></li>
          <li>
            <button class="settings-item" @click="selectAppStore">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M20 4H4v2h16V4zM4 20h4v-6h8v6h4v-8H4v8zm16-10l-.67-3.35a2.011 2.011 0 0 0-1.96-1.65H6.63c-.96 0-1.79.68-1.96 1.65L4 10v1c0 1.1.9 2 2 2s2-.9 2-2c0 1.1.9 2 2 2s2-.9 2-2c0 1.1.9 2 2 2s2-.9 2-2c0 1.1.9 2 2 2s2-.9 2-2v-1z" />
              </svg>
              <span>App store</span>
            </button>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.settings-menu {
  position: relative;
}

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
}

.settings-btn:hover {
  background: #4a6fa5;
  color: white;
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
  transform-origin: top left;
}

.settings-panel-enter-active,
.settings-panel-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.settings-panel-enter-from,
.settings-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

.settings-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.settings-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  color: #4a6fa5;
}

.settings-item svg {
  flex-shrink: 0;
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
</style>
