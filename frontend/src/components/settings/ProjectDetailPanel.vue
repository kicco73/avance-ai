<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import ChatView from '../chat/ChatView.vue'
import AppStoreFrozenPreview from '../appStore/AppStoreFrozenPreview.vue'
import { appStoreFileContentUrl } from '../../api.js'
import { setSkinCss, invalidateSkin } from '../../chatSkin.js'
import { setPreviewApp, appStorePreviewStore, historyLoaded, restartPreviewSession, stopPreviewSession } from '../../appStorePreviewStore.js'

const props = defineProps({
  app: { type: Object, required: true }
})

const emit = defineEmits(['edit', 'label', 'download', 'share', 'delete'])

const previewing = ref(false)

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

const deleteMenuOpen = ref(false)
const deleteMenuRootEl = ref(null)

function toggleDeleteMenu() {
  deleteMenuOpen.value = !deleteMenuOpen.value
}

function selectDeleteFromMenu() {
  deleteMenuOpen.value = false
  emit('delete', props.app.id)
}

function handleDeleteMenuDocumentClick(event) {
  if (deleteMenuOpen.value && deleteMenuRootEl.value && !deleteMenuRootEl.value.contains(event.target)) {
    deleteMenuOpen.value = false
  }
}

document.addEventListener('click', handleDeleteMenuDocumentClick, true)

async function loadSkinForApp(app) {
  if (!app) return
  try {
    const response = await fetch(appStoreFileContentUrl(app.id, 'index.css'), { credentials: 'include', cache: 'no-store' })
    setSkinCss(response.ok ? await response.text() : '', app.id)
  } catch {
    setSkinCss('', app.id)
  }
}

watch(() => props.app?.id, () => loadSkinForApp(props.app), { immediate: true })

async function quitPreview() {
  if (!previewing.value) return
  previewing.value = false
  await stopPreviewSession()
}

async function startPreview() {
  setPreviewApp(props.app.id)
  previewing.value = true
  await appStorePreviewStore.handleNewSession()
}

async function restartPreview() {
  await restartPreviewSession()
}

onBeforeUnmount(async () => {
  document.removeEventListener('click', handleDeleteMenuDocumentClick, true)
  await quitPreview()
  invalidateSkin()
})
</script>

<template>
  <div class="project-detail-header-row">
    <h2 class="project-detail-title">{{ appTitle(app) }}</h2>
    <div class="project-detail-menu" ref="deleteMenuRootEl">
      <button type="button" class="project-detail-menu-btn" title="More actions" @click="toggleDeleteMenu">⋮</button>
      <Transition name="project-detail-menu-panel">
        <ul v-if="deleteMenuOpen" class="project-detail-menu-list">
          <li>
            <button type="button" class="project-detail-menu-item" @click="selectDeleteFromMenu">Delete</button>
          </li>
        </ul>
      </Transition>
    </div>
  </div>
  <div class="project-detail-badges">
    <span class="project-detail-badge">MULTILINGUAL</span>
    <span v-if="app.reactions_enabled" class="project-detail-badge">REACTIONS</span>
  </div>
  <p class="project-detail-desc">{{ app.ui_description }}</p>

  <div class="project-detail-actions">
    <button
      type="button"
      class="project-detail-try-btn"
      :class="{ 'project-detail-try-btn-active': previewing }"
      @click="previewing ? quitPreview() : startPreview()"
    >{{ previewing ? 'Quit' : 'Test' }}</button>
    <button v-if="previewing" type="button" class="project-detail-secondary-btn" :disabled="!historyLoaded" @click="restartPreview">Restart</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('edit', app.id)">Edit</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('label', app.id)">Label</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('download', app.id)">Export</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('share', app.id)">Invite</button>
  </div>

  <div class="project-detail-try-panel">
    <AppStoreFrozenPreview v-if="!previewing || !historyLoaded" :app-id="app.id" :loading="previewing && !historyLoaded" />
    <ChatView v-if="previewing && historyLoaded" hide-sessions-panel :store="appStorePreviewStore" />
  </div>
</template>

<style scoped>
.project-detail-header-row {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}

.project-detail-title {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.project-detail-menu {
  position: relative;
  flex-shrink: 0;
}

.project-detail-menu-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.8rem;
  height: 1.8rem;
  border-radius: 6px;
  border: 1px solid #ddd;
  background: white;
  color: #555;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.project-detail-menu-btn:hover {
  background: #f0f0f0;
}

.project-detail-menu-list {
  position: absolute;
  top: calc(100% + 0.3rem);
  right: 0;
  min-width: 140px;
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 10;
}

.project-detail-menu-item {
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #c62828;
}

.project-detail-menu-item:hover {
  background: #fbeaea;
}

.project-detail-menu-panel-enter-active,
.project-detail-menu-panel-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.project-detail-menu-panel-enter-from,
.project-detail-menu-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

.project-detail-badges {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 1.2rem;
}

.project-detail-badge {
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  background: #eef3fa;
  color: #4a6fa5;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.project-detail-desc {
  margin: 0;
  min-height: 65px;
  color: #555;
  font-size: 0.9rem;
  white-space: pre-wrap;
}

.project-detail-actions {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}

.project-detail-try-panel {
  flex: 1;
  min-height: 300px;
  display: flex;
  flex-direction: column;
}

.project-detail-try-btn {
  padding: 0.4rem 1.2rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.project-detail-try-btn-active {
  border-color: #c62828;
  background: #c62828;
}

.project-detail-secondary-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #555;
  font-size: 0.85rem;
  cursor: pointer;
}
</style>
