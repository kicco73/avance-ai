<script setup>
import { onBeforeUnmount, ref } from 'vue'
import '../../styles/headerMenu.css'
import { deleteProjectBrokenWarning, getProjectBrokenWarnings } from '../../api.js'
import { chatChannel } from '../../chatChannel.js'
import { useOutsideClickClose } from '../../composables/useOutsideClickClose.js'

const props = defineProps({
  metadataById: { type: Object, required: true }
})

const emit = defineEmits(['open'])

const rootEl = ref(null)
const { open, toggle, close } = useOutsideClickClose(rootEl)
const warnings = ref([])

async function load() {
  try {
    const { warnings: rows } = await getProjectBrokenWarnings()
    warnings.value = rows
  } catch {
    // already surfaced via apiFetch
  }
}

// A project breaking or being fixed reaches every admin as a pushed
// system_warning frame, so the counter moves without waiting for the
// next refresh of this view. The broken frame carries no id of its own —
// one row per admin was written server-side, and reloading is what picks
// up this admin's row.
const unsubscribe = chatChannel.subscribe('system_warning', (frame) => {
  if (frame.kind === 'project_fixed') {
    warnings.value = warnings.value.filter((row) => row.project_id !== frame.project_id)
    return
  }
  load()
})

onBeforeUnmount(unsubscribe)

function toggleMenu() {
  toggle()
  if (open.value) load()
}

async function dismiss(warning) {
  try {
    await deleteProjectBrokenWarning(warning.id)
    warnings.value = warnings.value.filter((row) => row.id !== warning.id)
  } catch {
    // already surfaced via apiFetch
  }
}

function openWarning(warning) {
  close()
  emit('open', warning)
}

function projectTitle(id) {
  return props.metadataById[id]?.ui_label || id
}

function summary(warning) {
  return `${warning.file || 'index.yml'} no longer builds`
}

function formatTimestamp(timestamp) {
  return new Date(timestamp).toLocaleString()
}

load()
defineExpose({ refresh: load })
</script>

<template>
  <div v-if="warnings.length" ref="rootEl" class="header-menu">
    <button type="button" class="header-menu-btn warnings-btn" title="Broken project warnings" @click="toggleMenu">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 2 1 21h22L12 2zm0 6.5 6.53 11.5H5.47L12 8.5zM11 11v4h2v-4h-2zm0 5.5v2h2v-2h-2z" />
      </svg>
      <span class="warnings-count">{{ warnings.length }}</span>
    </button>
    <Transition name="header-menu-panel">
      <div v-if="open" class="header-menu-panel warnings-panel">
        <p class="warnings-title">Broken project warnings</p>
        <ul class="warnings-list">
          <li v-for="warning in warnings" :key="warning.id" class="warnings-item">
            <button type="button" class="warnings-item-open" title="Open the project at the problem" @click="openWarning(warning)">
              <span class="warnings-item-project">{{ projectTitle(warning.project_id) }}</span>
              <span class="warnings-item-time">{{ formatTimestamp(warning.timestamp) }}</span>
              <span class="warnings-item-message" :title="warning.message">{{ summary(warning) }}</span>
            </button>
            <button type="button" class="warnings-item-dismiss" title="Dismiss" @click="dismiss(warning)">×</button>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.warnings-btn {
  position: relative;
  border-color: #c0392b;
  color: #c0392b;
}

.warnings-btn:hover {
  background: #c0392b;
  color: white;
}

.warnings-count {
  position: absolute;
  top: -0.35rem;
  right: -0.35rem;
  min-width: 1.1rem;
  height: 1.1rem;
  padding: 0 0.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #c0392b;
  color: white;
  font-size: 0.65rem;
  font-weight: 700;
  line-height: 1;
}

.warnings-panel {
  min-width: 20rem;
  max-width: 26rem;
}

.warnings-title {
  margin: 0;
  padding: 0.6rem 0.9rem 0.4rem;
  font-size: 0.75rem;
  font-weight: 700;
  color: #333;
  border-bottom: 1px solid #eee;
}

.warnings-list {
  list-style: none;
  margin: 0;
  padding: 0.2rem 0;
  max-height: 18rem;
  overflow-y: auto;
}

.warnings-item {
  display: flex;
  align-items: flex-start;
  border-bottom: 1px solid #f3f3f3;
}

.warnings-item-open {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  text-align: left;
  cursor: pointer;
}

.warnings-item-open:hover {
  background: #fdf3f2;
}

.warnings-item-dismiss {
  flex-shrink: 0;
  margin: 0.4rem 0.5rem 0 0;
  width: 1.4rem;
  height: 1.4rem;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: none;
  color: #999;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.warnings-item-dismiss:hover {
  background: #eee;
  color: #333;
}

.warnings-item:last-child {
  border-bottom: none;
}

.warnings-item-project {
  font-size: 0.8rem;
  font-weight: 600;
  color: #c0392b;
}

.warnings-item-time {
  font-size: 0.65rem;
  color: #999;
}

.warnings-item-message {
  font-size: 0.75rem;
  color: #555;
}
</style>
