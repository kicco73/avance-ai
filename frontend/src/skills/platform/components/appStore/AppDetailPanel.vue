<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ChatView from '../../../../components/chat/ChatView.vue'
import ChatWaitingPanel from '../../../../components/chat/ChatWaitingPanel.vue'
import AppSnapshotGallery from './AppSnapshotGallery.vue'
import AppStoreFrozenPreview from './AppStoreFrozenPreview.vue'
import { postInstallApp, deleteInstallApp, postTrialSession, appStoreFileContentUrl } from '../../api.js'
import { confirmDialog } from '../../../../dialogStore.js'
import { holdSkin } from '../../../../chatSkin.js'
import { AppSkinSource } from '../../appSkinSource.js'
import { setPreviewApp, appStorePreviewStore, historyLoaded, restartPreviewSession, stopPreviewSession, endPreviewSession } from '../../appStorePreviewStore.js'
import { renderMarkdown } from '../../../../markdown.js'
import { usePreviewExpiry } from '../../../../composables/usePreviewExpiry.js'
import avanceLogoUrl from '../../../../assets/avance-logo.png'

const props = defineProps({
  app: { type: Object, required: true },
  showFreeBadge: { type: Boolean, default: true },
  hideInstallActions: { type: Boolean, default: false },
  tryButtonLabel: { type: String, default: 'Try me!' },
  showUninstallMenu: { type: Boolean, default: false }
})

const emit = defineEmits(['open'])

const installing = ref(false)
const previewing = ref(false)
const startingTrial = ref(false)
const trialError = ref(null)
const iconFailed = ref(false)

const trialsLeft = ref(props.app.trials_left ?? 0)
watch(() => props.app.trials_left, (left) => { trialsLeft.value = left ?? 0 })

const hasSnapshots = computed(() => Object.values(props.app.snapshot_files ?? {}).some((files) => files?.length))
const canTry = computed(() => trialsLeft.value > 0)
const canRestart = computed(() => historyLoaded.value && trialsLeft.value > 0)

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

const iconUrl = computed(() => (
  props.app.icon_file && !iconFailed.value ? appStoreFileContentUrl(props.app.id, props.app.icon_file) : avanceLogoUrl
))

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

const releaseSkin = holdSkin(new AppSkinSource(computed(() => props.app?.id ?? null)))

const { remainingLabel, elapsedPercent, expired, arm: armExpiryTimer, clear: clearExpiryTimer } = usePreviewExpiry()

watch(expired, async (hasExpired) => {
  if (!hasExpired) return
  clearExpiryTimer()
  await endPreviewSession()
})

async function quitPreview() {
  if (!previewing.value) return
  clearExpiryTimer()
  previewing.value = false
  expired.value = false
  await stopPreviewSession()
}

async function spendTrialSession() {
  trialError.value = null
  startingTrial.value = true
  try {
    const { trials_left: left } = await postTrialSession(props.app.id)
    trialsLeft.value = left
    props.app.trials_left = left
    props.app.trials_used = (props.app.trials_used ?? 0) + 1
    return true
  } catch {
    trialError.value = 'No test sessions left for this app.'
    trialsLeft.value = 0
    props.app.trials_left = 0
    return false
  } finally {
    startingTrial.value = false
  }
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
    await quitPreview()
  } catch {
  } finally {
    installing.value = false
  }
}

async function selectOpen() {
  await quitPreview()
  emit('open', props.app.id)
}

async function startPreview() {
  if (!await spendTrialSession()) return
  setPreviewApp(props.app.id)
  previewing.value = true
  expired.value = false
  armExpiryTimer()
  await appStorePreviewStore.handleNewSession()
}

async function restartPreview() {
  if (!await spendTrialSession()) return
  expired.value = false
  armExpiryTimer()
  await restartPreviewSession()
}

async function backToSnapshots() {
  await quitPreview()
}

onBeforeUnmount(async () => {
  releaseSkin()
  document.removeEventListener('click', handleUninstallMenuDocumentClick, true)
  await quitPreview()
})
</script>

<template>
  <div class="app-detail-header">
    <img :src="iconUrl" class="app-detail-icon" alt="" @error="iconFailed = true" />

    <div class="app-detail-identity">
      <h2 class="app-detail-title">{{ appTitle(app) }}</h2>
      <div class="app-detail-badges">
        <span v-if="showFreeBadge" class="app-store-badge">FREE</span>
        <span class="app-store-badge">MULTILINGUAL</span>
        <span v-if="app.reactions_enabled" class="app-store-badge">REACTIONS</span>
      </div>
    </div>

    <div class="app-detail-actions">
      <template v-if="previewing && !expired">
        <button
          v-if="canRestart"
          type="button"
          class="app-store-btn app-store-btn-flat"
          :disabled="startingTrial"
          @click="restartPreview"
        >Restart · {{ trialsLeft }} left</button>
        <button type="button" class="app-store-btn app-store-btn-quit" @click="quitPreview">Quit</button>
      </template>

      <template v-else-if="previewing && expired">
        <button
          v-if="canTry"
          type="button"
          class="app-store-try-btn"
          :disabled="startingTrial"
          @click="restartPreview"
        >Try again · {{ trialsLeft }} left</button>
        <button type="button" class="app-store-btn app-store-btn-flat" @click="backToSnapshots">Back to snapshots</button>
      </template>

      <template v-else>
        <button
          v-if="!hideInstallActions && app.installed"
          type="button"
          class="app-store-btn app-store-btn-primary"
          @click="selectOpen"
        >Open</button>
        <button
          v-else-if="canTry"
          type="button"
          class="app-store-try-btn"
          :disabled="startingTrial"
          @click="startPreview"
        >{{ tryButtonLabel }} · {{ trialsLeft }} left</button>
      </template>

      <template v-if="!hideInstallActions">
        <button
          v-if="!app.installed"
          type="button"
          class="app-store-btn"
          :class="expired ? 'app-store-btn-primary' : 'app-store-btn-ghost'"
          :disabled="installing"
          @click="toggleInstall"
        >Install</button>
        <button
          v-else-if="!showUninstallMenu"
          type="button"
          class="app-store-btn app-store-btn-danger"
          :disabled="installing"
          @click="toggleInstall"
        >Uninstall</button>
      </template>

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
  </div>

  <div class="app-detail-body">
    <div class="app-detail-stage">
      <div v-if="previewing" class="app-detail-trial-strip" :class="{ 'app-detail-trial-strip-over': expired }">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
          <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
        </svg>
        <span v-if="expired">Test session ended</span>
        <span v-else-if="!canRestart">Last test session</span>
        <span v-else>Test session</span>
        <div class="app-detail-trial-bar">
          <div class="app-detail-trial-bar-fill" :style="{ width: `${elapsedPercent}%` }"></div>
        </div>
        <span class="app-detail-trial-clock">{{ expired ? '0:00' : remainingLabel }}</span>
        <span class="app-detail-trial-hint">Nothing is saved to your account</span>
      </div>

      <div class="app-store-try-panel">
        <Transition name="app-store-try-chat">
          <AppSnapshotGallery
            v-if="!previewing && hasSnapshots"
            :app-id="app.id"
            :snapshot-files="app.snapshot_files"
          />
          <AppStoreFrozenPreview v-else-if="!previewing || !historyLoaded" :app-id="app.id" />
          <ChatView v-else hide-sessions-panel :store="appStorePreviewStore" />
        </Transition>
        <ChatWaitingPanel v-if="previewing && !historyLoaded" />

        <div v-if="previewing && expired" class="app-detail-expired-shield">
          <div class="app-detail-expired-card">
            <div class="app-detail-expired-text">
              <span class="app-detail-expired-title">Your test session has expired</span>
              <span class="app-detail-expired-body">
                Nothing from this test was saved.
                <template v-if="canTry">You have {{ trialsLeft }} more, or install the app to carry on with your own session.</template>
                <template v-else>Install the app to carry on with your own session.</template>
              </span>
            </div>
            <button type="button" class="app-store-btn app-store-btn-primary" :disabled="installing" @click="toggleInstall">Install</button>
          </div>
        </div>
      </div>
    </div>

    <div class="app-detail-rail">
      <p class="app-store-preview-desc">{{ app.ui_description }}</p>
      <hr class="app-detail-rail-divider" />
      <h3 class="app-detail-rail-heading">Summary</h3>
      <div v-if="app.ai_summary" class="app-store-preview-summary" v-html="renderMarkdown(app.ai_summary)"></div>
      <p v-else class="app-store-preview-summary-empty">No summary available yet.</p>
      <hr class="app-detail-rail-divider" />
      <dl class="app-detail-meta">
        <div class="app-detail-meta-row">
          <dt>Test sessions</dt>
          <dd :class="{ 'app-detail-meta-spent': !canTry }">{{ trialsLeft }} left · 5 min each</dd>
        </div>
      </dl>
      <p v-if="trialError" class="app-detail-trial-error">{{ trialError }}</p>
    </div>
  </div>
</template>

<style scoped>
.app-detail-header {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  gap: 0.9rem;
}

.app-detail-icon {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: 13px;
  object-fit: cover;
}

.app-detail-identity {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.app-detail-title {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.app-detail-badges {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 1.2rem;
}

.app-detail-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.app-detail-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 1rem;
}

.app-detail-stage {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.app-detail-rail {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  overflow-y: auto;
}

.app-detail-rail-divider {
  width: 100%;
  margin: 0;
  border: none;
  border-top: 1px solid #eee;
}

.app-detail-rail-heading {
  margin: 0;
  color: #8e8e93;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.app-detail-meta {
  margin: 0;
  display: flex;
  flex-direction: column;
}

.app-detail-meta-row {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.45rem 0;
  font-size: 0.8rem;
}

.app-detail-meta-row dt {
  color: #777;
}

.app-detail-meta-row dd {
  margin: 0;
  font-weight: 600;
  color: #2e7d32;
}

.app-detail-meta-spent {
  color: #8e8e93;
}

.app-detail-trial-error {
  margin: 0;
  color: #c62828;
  font-size: 0.78rem;
}

.app-detail-trial-strip {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  box-sizing: border-box;
  height: 38px;
  padding: 0 0.9rem;
  border-radius: 8px;
  background: #fdece0;
  color: #b3541e;
  font-size: 0.78rem;
  font-weight: 700;
}

.app-detail-trial-strip-over {
  background: #f2f2f7;
  color: #8e8e93;
}

.app-detail-trial-bar {
  flex: 1;
  height: 5px;
  border-radius: 3px;
  background: rgba(179, 84, 30, 0.2);
  overflow: hidden;
}

.app-detail-trial-strip-over .app-detail-trial-bar {
  background: #e5e5ea;
}

.app-detail-trial-bar-fill {
  height: 100%;
  background: #e07b39;
}

.app-detail-trial-strip-over .app-detail-trial-bar-fill {
  background: #c8ccd4;
}

.app-detail-trial-clock {
  font-variant-numeric: tabular-nums;
  font-size: 0.9rem;
}

.app-detail-trial-hint {
  font-weight: 500;
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

.app-store-btn {
  padding: 0.45rem 1.1rem;
  border-radius: 6px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.app-store-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.app-store-btn-primary {
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
}

.app-store-btn-primary:hover:not(:disabled) {
  background: #3d5c8a;
}

.app-store-btn-ghost {
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
}

.app-store-btn-ghost:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.app-store-btn-flat {
  border: 1px solid #ccc;
  background: white;
  color: #555;
}

.app-store-btn-quit,
.app-store-btn-danger {
  border: 1px solid #c62828;
}

.app-store-btn-quit {
  background: #c62828;
  color: white;
}

.app-store-btn-danger {
  background: white;
  color: #c62828;
}

.app-store-btn-danger:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.app-store-try-btn {
  padding: 0.45rem 1.1rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.app-store-try-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.app-store-try-panel {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.app-store-try-chat-enter-active,
.app-store-try-chat-leave-active {
  transition: opacity 0.12s ease;
}

.app-store-try-chat-enter-from,
.app-store-try-chat-leave-to {
  opacity: 0;
}

.app-store-try-chat-leave-active {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.app-detail-expired-shield {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  background: rgba(255, 255, 255, 0.35);
}

.app-detail-expired-card {
  display: flex;
  align-items: center;
  gap: 1.2rem;
  padding: 1rem 1.1rem;
  border-top: 1px solid #e5e5ea;
  background: white;
}

.app-detail-expired-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.app-detail-expired-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: #333;
}

.app-detail-expired-body {
  font-size: 0.8rem;
  line-height: 1.4;
  color: #777;
}

@media (max-width: 640px) {
  .app-detail-body {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) auto;
  }

  .app-detail-rail {
    overflow: visible;
  }

  .app-detail-trial-hint {
    display: none;
  }
}
</style>
