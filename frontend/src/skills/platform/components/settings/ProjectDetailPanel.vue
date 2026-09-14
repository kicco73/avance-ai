<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import ChatView from '../../../../components/chat/ChatView.vue'
import ChatWaitingPanel from '../../../../components/chat/ChatWaitingPanel.vue'
import AppStoreFrozenPreview from '../appStore/AppStoreFrozenPreview.vue'
import { holdSkin } from '../../../../chatSkin.js'
import { ProjectSkinSource } from '../../projectSkinSource.js'
import { setPreviewApp, appStorePreviewStore, historyLoaded, restartPreviewSession, stopPreviewSession } from '../../appStorePreviewStore.js'
import { projectActions } from '../../../registry.js'

const props = defineProps({
  projectId: { type: String, required: true },
  title: { type: String, required: true },
  description: { type: String, default: null },
  broken: { type: Object, default: null },
  publishedRevision: { type: Number, default: null },
  revision: { type: Number, default: null }
})

const emit = defineEmits(['edit', 'label', 'download', 'share', 'delete', 'publish', 'open-skill-view'])

const previewing = ref(false)

const untestableReason = computed(() => {
  if (props.broken?.published) return `Its published revision no longer builds:\n\n${props.broken.published}`
  if (props.publishedRevision == null) return "This project hasn't been published yet — publish it to test it here."
  return null
})

const deleteMenuOpen = ref(false)
const deleteMenuRootEl = ref(null)

function toggleDeleteMenu() {
  deleteMenuOpen.value = !deleteMenuOpen.value
}

function selectDeleteFromMenu() {
  deleteMenuOpen.value = false
  emit('delete', props.projectId)
}

function handleDeleteMenuDocumentClick(event) {
  if (deleteMenuOpen.value && deleteMenuRootEl.value && !deleteMenuRootEl.value.contains(event.target)) {
    deleteMenuOpen.value = false
  }
}

document.addEventListener('click', handleDeleteMenuDocumentClick, true)

const releaseSkin = holdSkin(new ProjectSkinSource(computed(() => props.projectId)))

async function quitPreview() {
  if (!previewing.value) return
  previewing.value = false
  await stopPreviewSession()
}

async function startPreview() {
  setPreviewApp(props.projectId)
  previewing.value = true
  await appStorePreviewStore.handleNewSession()
}

async function restartPreview() {
  await restartPreviewSession()
}

onBeforeUnmount(async () => {
  releaseSkin()
  document.removeEventListener('click', handleDeleteMenuDocumentClick, true)
  await quitPreview()
})
</script>

<template>
  <div class="project-detail-header-row">
    <div class="project-detail-title-row">
      <h2 class="project-detail-title">{{ title }}</h2>
      <span v-if="publishedRevision != null" class="project-detail-rev">rev. {{ publishedRevision }}</span>
    </div>
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
    <span v-if="broken?.published" class="project-detail-badge project-detail-badge-broken" :title="broken.published">BROKEN</span>
    <span v-if="broken?.draft" class="project-detail-badge project-detail-badge-draft-broken" :title="broken.draft">DRAFT BROKEN</span>
  </div>
  <p class="project-detail-desc">{{ description }}</p>

  <div class="project-detail-actions">
    <button
      type="button"
      class="project-detail-try-btn"
      :class="{ 'project-detail-try-btn-active': previewing }"
      :disabled="untestableReason !== null"
      :title="untestableReason ?? ''"
      @click="previewing ? quitPreview() : startPreview()"
    >{{ previewing ? 'Quit' : 'Test' }}</button>
    <button v-if="previewing" type="button" class="project-detail-secondary-btn" :disabled="!historyLoaded" @click="restartPreview">Restart</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('edit', projectId)">Edit</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('label', projectId)">Label</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('download', projectId)">Export</button>
    <button type="button" class="project-detail-secondary-btn" @click="emit('share', projectId)">Invite</button>
    <button
      type="button"
      class="project-detail-secondary-btn"
      title="Publish this project's current revision, then compile it if this backend can"
      @click="emit('publish', projectId)"
    >Publish</button>
    <component
      v-for="action in projectActions"
      :is="action.component"
      :key="action.id"
      :project-id="projectId"
      :published-revision="publishedRevision"
      :revision="revision"
      @activate="emit('open-skill-view', action.opens, projectId)"
    />
  </div>

  <div class="project-detail-try-panel">
    <p v-if="untestableReason" class="project-detail-untestable">{{ untestableReason }}</p>
    <AppStoreFrozenPreview v-else-if="!previewing || !historyLoaded" :app-id="projectId" />
    <ChatView v-if="previewing && historyLoaded" hide-sessions-panel :store="appStorePreviewStore" />
    <ChatWaitingPanel v-if="previewing && !historyLoaded" />
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

.project-detail-title-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  min-width: 0;
}

.project-detail-title {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.project-detail-rev {
  flex-shrink: 0;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  background: #eee;
  color: #888;
  font-size: 0.65rem;
  font-weight: 500;
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
  cursor: help;
}

.project-detail-badge-broken {
  background: #fdecea;
  color: #c0392b;
}

.project-detail-badge-draft-broken {
  background: #fdf1e3;
  color: #b06a00;
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
  position: relative;
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

.project-detail-try-btn:disabled {
  border-color: #ccc;
  background: #ccc;
  cursor: not-allowed;
}

.project-detail-untestable {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 1.5rem;
  border: 1px dashed #ddd;
  border-radius: 10px;
  background: #fafafa;
  color: #777;
  font-size: 0.9rem;
  text-align: center;
  white-space: pre-wrap;
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
