<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppHeader from '../AppHeader.vue'
import ProfileMenu from '../ProfileMenu.vue'
import ChatView from '../chat/ChatView.vue'
import AppStoreFrozenPreview from './AppStoreFrozenPreview.vue'
import { getAppStoreApps, postInstallApp, deleteInstallApp, appStoreFileContentUrl } from '../../api.js'
import { confirmDialog, infoDialog } from '../../dialogStore.js'
import { setSkinCss, invalidateSkin } from '../../chatSkin.js'
import { setPreviewApp, appStorePreviewStore, historyLoaded, restartPreviewSession, stopPreviewSession } from '../../appStorePreviewStore.js'
import avanceLogoUrl from '../../assets/avance-logo.png'

const props = defineProps({
  standalone: { type: Boolean, default: false },
  profile: { type: Object, default: null }
})

const emit = defineEmits(['close', 'open', 'profile', 'logout'])

const apps = ref([])
const loading = ref(true)
const selectedId = ref(null)
const installingId = ref(null)
const iconFailedById = ref({})
const previewing = ref(false)

const selectedApp = computed(() => apps.value.find((app) => app.id === selectedId.value) ?? null)

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

async function loadSkinForSelected() {
  const app = selectedApp.value
  if (!app) return
  try {
    const response = await fetch(appStoreFileContentUrl(app.id, 'index.css'), { credentials: 'include', cache: 'no-store' })
    setSkinCss(response.ok ? await response.text() : '', app.id)
  } catch {
    setSkinCss('', app.id)
  }
}

const PREVIEW_EXPIRY_SECONDS = 60
const remainingSeconds = ref(PREVIEW_EXPIRY_SECONDS)
const remainingLabel = computed(() => {
  const s = Math.max(0, remainingSeconds.value)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})
let expiryInterval = null

function clearExpiryTimer() {
  if (expiryInterval == null) return
  clearInterval(expiryInterval)
  expiryInterval = null
}

function armExpiryTimer() {
  clearExpiryTimer()
  remainingSeconds.value = PREVIEW_EXPIRY_SECONDS
  expiryInterval = setInterval(() => {
    remainingSeconds.value -= 1
    if (remainingSeconds.value <= 0) handlePreviewExpiry()
  }, 1000)
}

async function handlePreviewExpiry() {
  clearExpiryTimer()
  await quitPreview()
  await infoDialog({
    title: 'Test session expired',
    body: 'Your test session has expired. Thanks for trying out!'
  })
}

async function quitPreview() {
  if (!previewing.value) return
  clearExpiryTimer()
  previewing.value = false
  await stopPreviewSession()
}

async function selectApp(id) {
  if (id === selectedId.value) return
  await quitPreview()
  selectedId.value = id
  await loadSkinForSelected()
}

async function load() {
  loading.value = true
  try {
    apps.value = (await getAppStoreApps()).apps
    if (selectedId.value == null && apps.value.length) await selectApp(apps.value[0].id)
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

async function toggleInstall(app) {
  if (app.installed) {
    const ok = await confirmDialog({
      title: 'Uninstall',
      body: `Uninstall "${appTitle(app)}"? You'll lose access to its live chat until you install it again.`,
      okLabel: 'Uninstall',
      danger: true
    })
    if (!ok) return
    installingId.value = app.id
    try {
      await deleteInstallApp(app.id)
      app.installed = false
    } catch {
      // already surfaced via apiFetch
    } finally {
      installingId.value = null
    }
    return
  }
  const ok = await confirmDialog({
    title: 'Install',
    body: `Install "${appTitle(app)}"?`,
    okLabel: 'Install'
  })
  if (!ok) return
  installingId.value = app.id
  try {
    await postInstallApp(app.id)
    app.installed = true
  } catch {
    // already surfaced via apiFetch
  } finally {
    installingId.value = null
  }
}

async function selectOpen(app) {
  await quitPreview()
  emit('open', app.id)
}

async function startPreview() {
  if (!selectedApp.value) return
  setPreviewApp(selectedApp.value.id)
  previewing.value = true
  armExpiryTimer()
  await appStorePreviewStore.handleNewSession()
}

async function restartPreview() {
  armExpiryTimer()
  await restartPreviewSession()
}

onMounted(load)

onBeforeUnmount(async () => {
  await quitPreview()
  invalidateSkin()
})
</script>

<template>
  <div class="app-store-overlay">
    <AppHeader>
      <template #left>
        <button v-if="!standalone" type="button" class="app-header-icon-btn" title="Back" @click="emit('close')">«</button>
      </template>
      <template #center>
        <h2 class="app-header-title app-store-header-title">Store</h2>
      </template>
      <template #right>
        <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
      </template>
    </AppHeader>

    <div class="app-store-body">
      <div class="app-store-list">
        <p v-if="loading" class="app-store-status">Loading…</p>
        <p v-else-if="!apps.length" class="app-store-status">No apps available yet.</p>
        <button
          v-for="app in apps"
          :key="app.id"
          type="button"
          class="app-store-card"
          :class="{ 'app-store-card-active': app.id === selectedId }"
          @click="selectApp(app.id)"
        >
          <span class="app-store-card-icon">
            <img
              v-if="app.icon_file && !iconFailedById[app.id]"
              :src="appStoreFileContentUrl(app.id, app.icon_file)"
              alt=""
              @error="iconFailedById[app.id] = true"
            />
            <img v-else :src="avanceLogoUrl" class="app-store-card-fallback" alt="" />
          </span>
          <span class="app-store-card-body">
            <span class="app-store-card-title">{{ appTitle(app) }}</span>
            <span v-if="app.ui_description" class="app-store-card-desc">{{ app.ui_description }}</span>
          </span>
        </button>
      </div>

      <div class="app-store-preview">
        <template v-if="selectedApp">
          <h2 class="app-store-preview-title">{{ appTitle(selectedApp) }}</h2>
          <p class="app-store-preview-desc">{{ selectedApp.ui_description }}</p>

          <div class="app-store-preview-actions">
            <div class="app-store-preview-actions-left">
              <button
                v-if="!selectedApp.installed"
                type="button"
                class="app-store-btn app-store-btn-primary"
                :disabled="installingId === selectedApp.id"
                @click="toggleInstall(selectedApp)"
              >Install</button>
              <template v-else>
                <button type="button" class="app-store-btn app-store-btn-primary" @click="selectOpen(selectedApp)">Open</button>
                <button
                  type="button"
                  class="app-store-btn app-store-btn-danger"
                  :disabled="installingId === selectedApp.id"
                  @click="toggleInstall(selectedApp)"
                >Uninstall</button>
              </template>
            </div>
            <div class="app-store-preview-actions-right">
              <button v-if="previewing" type="button" class="app-store-try-restart-btn" :disabled="!historyLoaded" @click="restartPreview">Restart</button>
              <button
                type="button"
                class="app-store-try-btn"
                :class="{ 'app-store-try-btn-active': previewing }"
                @click="previewing ? quitPreview() : startPreview()"
              >{{ previewing ? remainingLabel : 'Try me!' }}</button>
            </div>
          </div>

          <div class="app-store-try-panel">
            <AppStoreFrozenPreview v-if="!previewing || !historyLoaded" :app-id="selectedApp.id" :loading="previewing && !historyLoaded" />
            <ChatView v-if="previewing && historyLoaded" hide-sessions-panel :store="appStorePreviewStore" />
          </div>
        </template>
        <p v-else-if="!loading" class="app-store-status">Select an app to see its details.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-store-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  background: white;
}

.app-store-header-title {
  color: #4a6fa5;
}

.app-store-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 1rem;
  padding: 1rem;
}

.app-store-list {
  flex: none;
  width: 340px;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.app-store-card {
  flex-shrink: 0;
  box-sizing: border-box;
  display: flex;
  align-items: stretch;
  gap: 0.6rem;
  width: 100%;
  padding: 0.5rem 0.7rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.app-store-card-active {
  border-color: #4a6fa5;
  background: #eef3fa;
}

.app-store-card-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.app-store-card-icon img {
  width: 65px;
  height: 65px;
  border-radius: 15px;
  object-fit: cover;
}

.app-store-card-icon img.app-store-card-fallback {
  object-fit: contain;
  opacity: 0.25;
}

.app-store-card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.1rem;
}

.app-store-card-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-store-card-desc {
  font-size: 0.75rem;
  color: #777;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.app-store-preview {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
}

.app-store-preview-title {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.app-store-preview-desc {
  margin: 0;
  min-height: 65px;
  color: #555;
  font-size: 0.9rem;
  white-space: pre-wrap;
}

.app-store-preview-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}

.app-store-preview-actions-left,
.app-store-preview-actions-right {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.app-store-btn {
  padding: 0.45rem 1.1rem;
  border-radius: 6px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.app-store-btn-primary {
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
}

.app-store-btn-primary:hover {
  background: #3d5c8a;
}

.app-store-btn-danger {
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
}

.app-store-btn-danger:hover {
  background: #c62828;
  color: white;
}

.app-store-try-panel {
  flex: 1;
  min-height: 300px;
  display: flex;
  flex-direction: column;
}

.app-store-try-btn {
  padding: 0.4rem 1.2rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.app-store-try-btn-active {
  border-color: #c62828;
  background: #c62828;
}

.app-store-try-restart-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #555;
  font-size: 0.85rem;
  cursor: pointer;
}

.app-store-status {
  color: #888;
  font-size: 0.9rem;
}
</style>
