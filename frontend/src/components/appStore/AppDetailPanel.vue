<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ChatView from '../chat/ChatView.vue'
import AppStoreFrozenPreview from './AppStoreFrozenPreview.vue'
import { postInstallApp, deleteInstallApp, appStoreFileContentUrl } from '../../api.js'
import { confirmDialog, infoDialog } from '../../dialogStore.js'
import { setSkinCss, invalidateSkin } from '../../chatSkin.js'
import { setPreviewApp, appStorePreviewStore, historyLoaded, restartPreviewSession, stopPreviewSession } from '../../appStorePreviewStore.js'
import { renderMarkdown } from '../../markdown.js'

const props = defineProps({
  app: { type: Object, required: true },
  showFreeBadge: { type: Boolean, default: true },
  hideInstallActions: { type: Boolean, default: false },
  tryButtonLabel: { type: String, default: 'Try me!' },
  timedSession: { type: Boolean, default: true },
  showUninstallMenu: { type: Boolean, default: false }
})

const emit = defineEmits(['open'])

const installing = ref(false)
const previewing = ref(false)

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

const uninstallMenuOpen = ref(false)
const uninstallMenuRootEl = ref(null)

function toggleUninstallMenu() {
  uninstallMenuOpen.value = !uninstallMenuOpen.value
}

async function selectUninstallFromMenu() {
  uninstallMenuOpen.value = false
  await toggleInstall()
}

function handleUninstallMenuDocumentClick(event) {
  if (uninstallMenuOpen.value && uninstallMenuRootEl.value && !uninstallMenuRootEl.value.contains(event.target)) {
    uninstallMenuOpen.value = false
  }
}

document.addEventListener('click', handleUninstallMenuDocumentClick, true)

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

const PREVIEW_EXPIRY_SECONDS = 5 * 60
const PREVIEW_COUNTDOWN_THRESHOLD_SECONDS = 59
const remainingSeconds = ref(PREVIEW_EXPIRY_SECONDS)
const remainingLabel = computed(() => {
  const s = Math.max(0, remainingSeconds.value)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})
const quitButtonLabel = computed(() => (
  props.timedSession && remainingSeconds.value <= PREVIEW_COUNTDOWN_THRESHOLD_SECONDS ? remainingLabel.value : 'Quit'
))
let expiryInterval = null

function clearExpiryTimer() {
  if (expiryInterval == null) return
  clearInterval(expiryInterval)
  expiryInterval = null
}

function armExpiryTimer() {
  clearExpiryTimer()
  if (!props.timedSession) return
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
    body: 'Your test session has expired. Thanks for trying out!',
    okLabel: 'Close'
  })
}

async function quitPreview() {
  if (!previewing.value) return
  clearExpiryTimer()
  previewing.value = false
  await stopPreviewSession()
}

async function toggleInstall() {
  const app = props.app
  if (app.installed) {
    const ok = await confirmDialog({
      title: 'Uninstall',
      body: `Uninstall "${appTitle(app)}"? You'll also permanently lose all data recorded for it.`,
      okLabel: 'Uninstall',
      danger: true
    })
    if (!ok) return
    installing.value = true
    try {
      await deleteInstallApp(app.id)
      app.installed = false
    } catch {
      // already surfaced via apiFetch
    } finally {
      installing.value = false
    }
    return
  }
  const ok = await confirmDialog({
    title: 'Install',
    body: `Install "${appTitle(app)}"?`,
    okLabel: 'Install'
  })
  if (!ok) return
  installing.value = true
  try {
    await postInstallApp(app.id)
    app.installed = true
  } catch {
    // already surfaced via apiFetch
  } finally {
    installing.value = false
  }
}

async function selectOpen() {
  await quitPreview()
  emit('open', props.app.id)
}

async function startPreview() {
  setPreviewApp(props.app.id)
  previewing.value = true
  armExpiryTimer()
  await appStorePreviewStore.handleNewSession()
}

async function restartPreview() {
  armExpiryTimer()
  await restartPreviewSession()
}

onBeforeUnmount(async () => {
  document.removeEventListener('click', handleUninstallMenuDocumentClick, true)
  await quitPreview()
  invalidateSkin()
})
</script>

<template>
  <div class="app-store-preview-header-row">
    <h2 class="app-store-preview-title">{{ appTitle(app) }}</h2>
    <div v-if="showUninstallMenu" class="app-store-preview-menu" ref="uninstallMenuRootEl">
      <button type="button" class="app-store-preview-menu-btn" title="More actions" @click="toggleUninstallMenu">⋮</button>
      <Transition name="app-store-preview-menu-panel">
        <ul v-if="uninstallMenuOpen" class="app-store-preview-menu-list">
          <li>
            <button type="button" class="app-store-preview-menu-item" @click="selectUninstallFromMenu">Uninstall</button>
          </li>
        </ul>
      </Transition>
    </div>
  </div>
  <div class="app-store-preview-badges">
    <span v-if="showFreeBadge" class="app-store-badge">FREE</span>
    <span class="app-store-badge">MULTILINGUAL</span>
    <span v-if="app.reactions_enabled" class="app-store-badge">REACTIONS</span>
  </div>
  <p class="app-store-preview-desc">{{ app.ui_description }}</p>

  <div v-if="app.ai_summary" class="app-store-preview-summary" v-html="renderMarkdown(app.ai_summary)"></div>
  <p v-else class="app-store-preview-summary-empty">No summary available yet.</p>

  <div class="app-store-preview-actions">
    <template v-if="!hideInstallActions">
      <button
        v-if="!app.installed"
        type="button"
        class="app-store-btn app-store-btn-primary"
        :disabled="installing"
        @click="toggleInstall"
      >Install</button>
      <template v-else>
        <button type="button" class="app-store-btn app-store-btn-primary" @click="selectOpen">Open</button>
        <button
          type="button"
          class="app-store-btn app-store-btn-danger"
          :disabled="installing"
          @click="toggleInstall"
        >Uninstall</button>
      </template>
    </template>
    <template v-if="hideInstallActions || !app.installed || previewing">
      <button
        type="button"
        class="app-store-try-btn"
        :class="{ 'app-store-try-btn-active': previewing }"
        @click="previewing ? quitPreview() : startPreview()"
      >{{ previewing ? quitButtonLabel : tryButtonLabel }}</button>
      <button v-if="previewing" type="button" class="app-store-try-restart-btn" :disabled="!historyLoaded" @click="restartPreview">Restart</button>
    </template>
  </div>

  <div class="app-store-try-panel">
    <AppStoreFrozenPreview v-if="!previewing || !historyLoaded" :app-id="app.id" :loading="previewing && !historyLoaded" />
    <ChatView v-if="previewing && historyLoaded" hide-sessions-panel :store="appStorePreviewStore" />
  </div>
</template>

<style scoped>
.app-store-preview-header-row {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}

.app-store-preview-title {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.app-store-preview-menu {
  position: relative;
  flex-shrink: 0;
}

.app-store-preview-menu-btn {
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

.app-store-preview-menu-btn:hover {
  background: #f0f0f0;
}

.app-store-preview-menu-list {
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

.app-store-preview-menu-item {
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #c62828;
}

.app-store-preview-menu-item:hover {
  background: #fbeaea;
}

.app-store-preview-menu-panel-enter-active,
.app-store-preview-menu-panel-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.app-store-preview-menu-panel-enter-from,
.app-store-preview-menu-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

.app-store-preview-badges {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 1.2rem;
}

.app-store-badge {
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  background: #eef3fa;
  color: #4a6fa5;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.app-store-preview-desc {
  margin: 0;
  min-height: 65px;
  color: #555;
  font-size: 0.9rem;
  white-space: pre-wrap;
}

.app-store-preview-summary {
  color: #555;
  font-size: 0.9rem;
  line-height: 1.5;
}

.app-store-preview-summary-empty {
  margin: 0;
  color: #999;
  font-size: 0.85rem;
  font-style: italic;
}

.app-store-preview-actions {
  flex-shrink: 0;
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
</style>
