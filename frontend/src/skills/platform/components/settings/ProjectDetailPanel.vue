<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ChatView from '../../../../components/chat/ChatView.vue'
import ChatWaitingPanel from '../../../../components/chat/ChatWaitingPanel.vue'
import AppStoreFrozenPreview from '../appStore/AppStoreFrozenPreview.vue'
import InspectorSignalsTab from '../inspector/InspectorSignalsTab.vue'
import TimelineChart from './TimelineChart.vue'
import { holdSkin } from '../../../../chatSkin.js'
import { valuesToSignalValues } from '../../../../testTimeline.js'
import { ProjectSkinSource } from '../../projectSkinSource.js'
import { setPreviewApp, appStorePreviewStore, historyLoaded, restartPreviewSession, stopPreviewSession } from '../../appStorePreviewStore.js'
import { getUserLatestSignals } from '../../api.js'
import { projectActions } from '../../../registry.js'

const props = defineProps({
  app: { type: Object, required: true },
  profile: { type: Object, default: null },
  publishedRevision: { type: Number, default: null },
  revision: { type: Number, default: null }
})

const emit = defineEmits(['edit', 'label', 'download', 'share', 'delete', 'publish', 'open-skill-view'])

const previewing = ref(false)

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

const tabs = [{ id: 'info', label: 'Info' }, { id: 'signals', label: 'Signals' }]
const activeTab = ref('info')

const signalsUsername = computed(() => props.profile?.email ?? props.profile?.id ?? null)
const signalColorMap = ref(null)
const latestSignals = ref({ last_session: null, session_id: null, values: null })
const latestSignalsLoading = ref(false)
const latestSignalValues = computed(() => valuesToSignalValues(latestSignals.value.values))
let latestSignalsLoaded = false

async function loadLatestSignals() {
  if (!signalsUsername.value) return
  latestSignalsLoading.value = true
  try {
    latestSignals.value = await getUserLatestSignals(props.app.id, signalsUsername.value)
  } catch {
    latestSignals.value = { last_session: null, session_id: null, values: null }
  } finally {
    latestSignalsLoading.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab !== 'info') quitPreview()
  if (tab === 'signals' && !latestSignalsLoaded) {
    latestSignalsLoaded = true
    loadLatestSignals()
  }
})

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

const releaseSkin = holdSkin(new ProjectSkinSource(computed(() => props.app?.id ?? null)))

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
  releaseSkin()
  document.removeEventListener('click', handleDeleteMenuDocumentClick, true)
  await quitPreview()
})
</script>

<template>
  <div class="project-detail-tabbar">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      class="project-detail-tab"
      :class="{ 'project-detail-tab-active': activeTab === tab.id }"
      @click="activeTab = tab.id"
    >{{ tab.label }}</button>
  </div>

  <template v-if="activeTab === 'info'">
    <div class="project-detail-header-row">
      <div class="project-detail-title-row">
        <h2 class="project-detail-title">{{ appTitle(app) }}</h2>
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
      <span class="project-detail-badge">MULTILINGUAL</span>
      <span v-if="app.reactions_enabled" class="project-detail-badge">REACTIONS</span>
      <span v-if="app.compiled" class="project-detail-badge">COMPILED</span>
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
      <button
        v-if="revision !== publishedRevision"
        type="button"
        class="project-detail-secondary-btn"
        title="Publish this project's current revision, then compile it if this backend can"
        @click="emit('publish', app.id)"
      >Publish</button>
      <component
        v-for="action in projectActions"
        :is="action.component"
        :key="action.id"
        :project-id="app.id"
        :published-revision="publishedRevision"
        :revision="revision"
        @activate="emit('open-skill-view', action.opens, app.id)"
      />
    </div>

    <div class="project-detail-try-panel">
      <AppStoreFrozenPreview v-if="!previewing || !historyLoaded" :app-id="app.id" />
      <ChatView v-if="previewing && historyLoaded" hide-sessions-panel :store="appStorePreviewStore" />
      <ChatWaitingPanel v-if="previewing && !historyLoaded" />
    </div>
  </template>

  <div v-else-if="activeTab === 'signals'" class="project-detail-signals-tab">
    <p v-if="!signalsUsername" class="project-detail-status">Your profile has no email on file.</p>
    <template v-else>
      <div class="project-detail-trends-block">
        <TimelineChart :project-id="app.id" :username="signalsUsername" @colors="signalColorMap = $event" />
      </div>
      <p v-if="latestSignalsLoading" class="project-detail-status">Loading…</p>
      <p v-else-if="!latestSignals.last_session" class="project-detail-status">
        You have no live sessions in this app yet.
      </p>
      <InspectorSignalsTab
        v-else
        :project-id="app.id"
        :signal-values="latestSignalValues"
        :session-id="latestSignals.session_id"
        :signal-colors="signalColorMap"
      />
    </template>
  </div>
</template>

<style scoped>
.project-detail-tabbar {
  flex-shrink: 0;
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid #ddd;
}

.project-detail-tab {
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  color: #666;
}

.project-detail-tab:hover {
  color: #333;
}

.project-detail-tab-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
}

.project-detail-signals-tab {
  flex: 1;
  min-height: 300px;
  display: flex;
  flex-direction: column;
}

.project-detail-trends-block {
  width: 100%;
  height: 200px;
  max-height: 200px;
  flex-shrink: 0;
  margin-bottom: 1rem;
}

.project-detail-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

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
