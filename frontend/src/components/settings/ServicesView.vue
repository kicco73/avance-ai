<script setup>
// Settings > Manage services: read-only view of .config.yml's own
// service sections (see backend AppConfig.public_services_snapshot),
// one tab per section — plus the Database tab's own backup/restore and
// wipe-all-live-sessions actions (moved here from the Settings menu and
// Manage projects respectively), and the live chat model picker (moved
// here from Manage projects' own header).
import { onMounted, ref } from 'vue'
import AppHeader from '../AppHeader.vue'
import ProfileMenu from '../ProfileMenu.vue'
import ServicesProviderCard from './ServicesProviderCard.vue'
import StatusToggleButton from './StatusToggleButton.vue'
import { getServicesConfig } from '../../api.js'
import { confirmDialog } from '../../dialogStore.js'
import { liveModelStore } from '../../chatStore.js'

defineProps({
  // ProfileMenu.vue's own avatar/name — App.vue already fetched this once
  // during boot, passed straight through so this view can show the same
  // topbar avatar every other full-screen view does.
  profile: { type: Object, default: null }
})

// download-backup/restore-backup/wipe-live-sessions are a plain
// pass-through — App.vue owns the actual fetch + confirmation logic for
// backup restore, same as it always has; this view confirms the wipe
// itself, same as Manage projects' own per-project wipe used to.
const emit = defineEmits(['close', 'download-backup', 'restore-backup', 'wipe-live-sessions', 'profile', 'logout'])

const TABS = [
  { id: 'ai', label: 'AI' },
  { id: 'chat', label: 'Chat' },
  { id: 'testing', label: 'Testing' },
  { id: 'talk', label: 'Talk' },
  { id: 'listen', label: 'Listen' },
  { id: 'whatsapp', label: 'WhatsApp' },
  { id: 'database', label: 'Data' }
]

const WHATSAPP_MASKED_FIELDS = ['verify-token', 'app-secret', 'access-token']
const WHATSAPP_PLAIN_FIELDS = ['phone-number-id', 'phone-number', 'graph-version']
const revealedWhatsAppFields = ref(Object.fromEntries(WHATSAPP_MASKED_FIELDS.map((key) => [key, false])))

const activeTab = ref(TABS[0].id)
const services = ref(null)
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    services.value = await getServicesConfig()
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}
onMounted(load)

function fieldLabel(key) {
  return key.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

// Any path that ends in one specific provider pinned (auto=false) loses
// the same thing: no more automatic fallback if that provider fails or
// runs out of tokens. Shared verbatim by selectModelWithConfirm's own
// index!=null branch and toggleAutoLive's disable path below, so a
// manual provider switch and an explicit "turn cascading off" read as
// the same warning rather than two different-sounding ones.
const NO_FALLBACK_WARNING =
  'If the current provider fails or runs out of tokens, live chat will no longer automatically fall back to the next one and the service will stay broken. Continue?'

// Shared by both the Auto-live checkbox (index null) and each provider's
// own play button (its own index) below — same no-op guard ModelMenu.vue's
// own select() used to run before ever reaching this, now needed here
// directly since neither caller goes through that component anymore.
async function selectModelWithConfirm(index) {
  if (liveModelStore.selectionLoading.value) return
  const alreadySelected = index === (liveModelStore.auto.value ? null : liveModelStore.currentIndex.value)
  if (alreadySelected) return
  const ok = index == null
    ? await confirmDialog({
        title: 'Enable auto-live cascading',
        body: `Switch the live chat provider to "${liveModelStore.autoLabel ?? 'Auto'}"?`,
        okLabel: 'Switch'
      })
    : await confirmDialog({
        title: 'Change provider',
        body: NO_FALLBACK_WARNING,
        okLabel: 'Switch',
        danger: true
      })
  if (!ok) return
  await liveModelStore.select(index)
}

// The Auto-live checkbox's own click handler — turning it on is just
// selectModelWithConfirm(null) (its own "Switch to Auto?" confirm above),
// but turning it off shows the same NO_FALLBACK_WARNING a manual provider
// switch does — same real consequence either way. Whichever provider
// auto-live cascading was actually using stays exactly where it is — this
// only pins it explicitly, it never re-picks.
async function toggleAutoLive() {
  if (liveModelStore.selectionLoading.value) return
  if (!liveModelStore.auto.value) {
    await selectModelWithConfirm(null)
    return
  }
  const ok = await confirmDialog({
    title: 'Disable auto-live cascading',
    body: NO_FALLBACK_WARNING,
    okLabel: 'Disable',
    danger: true
  })
  if (!ok) return
  await liveModelStore.select(liveModelStore.currentIndex.value)
}

// Whichever provider is actually serving right now, auto-picked by the
// cascade or manually pinned either way — same "which one is really in
// effect" ModelMenu.vue's own checkmark used to show next to "Auto
// (currentLabel)" when cascading was on.
function isProviderActive(index) {
  return liveModelStore.currentIndex.value === index
}

function providerStatusTitle(index) {
  return isProviderActive(index) ? 'Active provider' : 'Set as the active provider'
}

async function selectWipeAllLiveSessions() {
  const ok = await confirmDialog({
    title: 'Wipe all live sessions',
    body: 'Delete every live conversation across every project? This cannot be undone.',
    okLabel: 'Wipe',
    danger: true
  })
  if (!ok) return
  emit('wipe-live-sessions')
}
</script>

<template>
  <div class="services-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="emit('close')">«</button>
      </template>
      <template #center>
        <h2 class="app-header-title services-header-title">Services</h2>
      </template>
      <template #right>
        <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
      </template>
    </AppHeader>

    <div class="services-tabs">
      <button
        v-for="tab in TABS"
        :key="tab.id"
        class="services-tab-btn"
        :class="{ 'services-tab-btn-active': activeTab === tab.id }"
        @click="activeTab = tab.id"
      >
        <svg v-if="tab.id === 'ai'" class="services-tab-ai-icon" viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
          <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
        </svg>
        {{ tab.label }}
      </button>
    </div>

    <div class="services-body">
      <p v-if="loading" class="services-status">Loading…</p>
      <template v-else-if="services">
        <div v-show="activeTab === 'chat'" class="services-panel">
          <div v-for="[key, value] in Object.entries(services.chat)" :key="key" class="services-field">
            <label class="services-field-label">{{ fieldLabel(key) }}</label>
            <input class="services-field-input" type="text" :value="value" disabled />
          </div>
        </div>

        <div v-show="activeTab === 'testing'" class="services-panel">
          <div v-for="[key, value] in Object.entries(services.testing)" :key="key" class="services-field">
            <label class="services-field-label">{{ fieldLabel(key) }}</label>
            <input class="services-field-input" type="text" :value="value" disabled />
          </div>
        </div>

        <div v-show="activeTab === 'ai'" class="services-panel">
          <label class="services-checkbox-field services-checkbox-field-active">
            <input type="checkbox" :checked="liveModelStore.auto.value" @click.prevent="toggleAutoLive" />
            Auto-live cascading enabled
          </label>
          <div class="services-field">
            <label class="services-field-label">Max output tokens</label>
            <input class="services-field-input" type="text" :value="services.ai['max-output-tokens']" disabled />
          </div>
          <div v-for="(provider, i) in services.ai.providers" :key="i" class="services-provider-row">
            <ServicesProviderCard class="services-provider-row-card" :provider="provider" />
            <StatusToggleButton
              :status="isProviderActive(i) ? 'running' : 'manually_paused'"
              :disabled="isProviderActive(i) || liveModelStore.selectionLoading.value"
              :title="providerStatusTitle(i)"
              @click="selectModelWithConfirm(i)"
            />
          </div>
        </div>

        <div v-show="activeTab === 'talk'" class="services-panel">
          <label class="services-checkbox-field">
            <input type="checkbox" :checked="services.talk.enabled" disabled />
            Service enabled
          </label>
          <ServicesProviderCard v-for="(provider, i) in services.talk.providers" :key="i" :provider="provider" />
        </div>

        <div v-show="activeTab === 'listen'" class="services-panel">
          <label class="services-checkbox-field">
            <input type="checkbox" :checked="services.listen.enabled" disabled />
            Service enabled
          </label>
          <ServicesProviderCard v-for="(provider, i) in services.listen.providers" :key="i" :provider="provider" />
        </div>

        <div v-show="activeTab === 'whatsapp'" class="services-panel">
          <label class="services-checkbox-field">
            <input type="checkbox" :checked="services.whatsapp.enabled" disabled />
            Service enabled
          </label>
          <template v-if="services.whatsapp.enabled">
            <div v-for="key in WHATSAPP_MASKED_FIELDS" :key="key" class="services-field">
              <label class="services-field-label">{{ fieldLabel(key) }}</label>
              <div class="services-field-masked-row">
                <input
                  class="services-field-input"
                  :type="revealedWhatsAppFields[key] ? 'text' : 'password'"
                  :value="services.whatsapp[key]"
                  disabled
                />
                <button
                  type="button"
                  class="services-reveal-btn"
                  :title="revealedWhatsAppFields[key] ? 'Hide' : 'Show'"
                  @click="revealedWhatsAppFields[key] = !revealedWhatsAppFields[key]"
                >{{ revealedWhatsAppFields[key] ? 'Hide' : 'Show' }}</button>
              </div>
            </div>
            <div v-for="key in WHATSAPP_PLAIN_FIELDS" :key="key" class="services-field">
              <label class="services-field-label">{{ fieldLabel(key) }}</label>
              <input class="services-field-input" type="text" :value="services.whatsapp[key]" disabled />
            </div>
            <label class="services-checkbox-field">
              <input type="checkbox" :checked="services.whatsapp['mark-read']" disabled />
              Mark messages as read
            </label>
          </template>
        </div>

        <div v-show="activeTab === 'database'" class="services-panel">
          <div v-for="[key, value] in Object.entries(services.database)" :key="key" class="services-field">
            <label class="services-field-label">{{ fieldLabel(key) }}</label>
            <input class="services-field-input" type="text" :value="value" disabled />
          </div>

          <div class="services-section">
            <div class="services-actions-row">
              <button type="button" class="services-action-btn" @click="emit('download-backup')">Download backup</button>
              <label class="services-action-btn services-restore-label">
                Restore backup...
                <input
                  type="file"
                  accept=".sqlite"
                  class="services-restore-input"
                  @change="(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) emit('restore-backup', f) }"
                />
              </label>
              <button type="button" class="services-action-btn services-action-btn-danger" @click="selectWipeAllLiveSessions">Wipe all live sessions</button>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.services-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.services-header-title {
  color: #4a6fa5;
}

.services-tabs {
  display: flex;
  gap: 0.25rem;
  padding: 0.5rem 1.25rem 0;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.services-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #666;
}

.services-tab-ai-icon {
  flex-shrink: 0;
  color: #8b5cf6;
}

.services-tab-btn:hover {
  color: #333;
}

.services-tab-btn-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
}

.services-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1.25rem;
  padding-bottom: calc(1.25rem + var(--safe-area-bottom));
}

.services-status {
  margin: 0;
  font-size: 0.9rem;
  color: #666;
}

.services-panel {
  max-width: 640px;
}

.services-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0 0 0.75rem;
  max-width: 420px;
}

.services-field-label {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #777;
}

.services-field-input {
  width: 100%;
  box-sizing: border-box;
  padding: 0.4rem 0.6rem;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #f5f5f7;
  color: #333;
  font: inherit;
  font-size: 0.85rem;
}

.services-field-input:disabled {
  opacity: 1;
  cursor: default;
  -webkit-text-fill-color: #333;
}

.services-field-masked-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.services-field-masked-row .services-field-input {
  flex: 1;
  min-width: 0;
}

.services-reveal-btn {
  flex-shrink: 0;
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  border: 1px solid #ddd;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.8rem;
}

.services-reveal-btn:hover {
  background: #4a6fa5;
  color: white;
  border-color: #4a6fa5;
}

.services-checkbox-field {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  font-size: 0.85rem;
  color: #333;
  cursor: default;
}

.services-checkbox-field input[type="checkbox"] {
  width: 1rem;
  height: 1rem;
  accent-color: #4a6fa5;
}

/* Unlike the other (read-only) checkbox-fields, this one is a real
   action — clicking it selects Auto-live cascading, same as picking
   "Auto" used to in the old ModelMenu.vue dropdown. */
.services-checkbox-field-active {
  cursor: pointer;
}

.services-checkbox-field-active input[type="checkbox"] {
  cursor: pointer;
}

/* The play/pause status button sits outside ServicesProviderCard.vue's
   own card, same layout as ManageProjectsView.vue's own project-card +
   status-btn row, not embedded inside the card. */
.services-provider-row {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  margin: 0.75rem 0;
}

.services-provider-row-card {
  flex: 1;
  min-width: 0;
}

.services-provider-row-card :deep(.inspector-detail-card) {
  margin: 0;
}

.services-section {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  margin: 1.25rem 0 0.75rem;
  padding-top: 1rem;
  border-top: 1px solid #eee;
}

.services-actions-row {
  display: flex;
  gap: 0.6rem;
}

.services-action-btn {
  display: inline-flex;
  align-items: center;
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.85rem;
}

.services-action-btn:hover {
  background: #4a6fa5;
  color: white;
}

.services-restore-label {
  position: relative;
}

.services-restore-input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.services-action-btn-danger {
  border-color: #c62828;
  color: #c62828;
}

.services-action-btn-danger:hover {
  background: #c62828;
  color: white;
}
</style>
