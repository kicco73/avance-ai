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
import ModelMenu from '../ModelMenu.vue'
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
  { id: 'chat', label: 'Chat' },
  { id: 'testing', label: 'Testing' },
  { id: 'ai', label: 'AI' },
  { id: 'talk', label: 'Talk' },
  { id: 'listen', label: 'Listen' },
  { id: 'database', label: 'Database' }
]

const activeTab = ref('chat')
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

// Every field on a provider entry except its own description (shown
// separately, as prose) — works unchanged across ai/talk/listen's
// slightly different provider shapes (url/modes/language are each
// present on only one of the three).
function providerFields(provider) {
  return Object.entries(provider)
    .filter(([key, value]) => key !== 'ui-description' && value != null && value !== '')
    .map(([key, value]) => [fieldLabel(key), Array.isArray(value) ? value.join(', ') : String(value)])
}

async function selectModelWithConfirm(index) {
  const label = index == null ? (liveModelStore.autoLabel ?? 'Auto') : (liveModelStore.models.value[index]?.ui_label ?? 'this model')
  const ok = await confirmDialog({
    title: 'Change model',
    body: `Switch the live chat model to "${label}"?`,
    okLabel: 'Switch'
  })
  if (!ok) return
  await liveModelStore.select(index)
}

const confirmingLiveModelStore = { ...liveModelStore, select: selectModelWithConfirm }

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
      >{{ tab.label }}</button>
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
          <div class="services-provider-card">
            <div class="services-provider-title">Model</div>
            <ModelMenu :model-store="confirmingLiveModelStore" />
          </div>
          <div class="services-field">
            <label class="services-field-label">Max output tokens</label>
            <input class="services-field-input" type="text" :value="services.ai['max-output-tokens']" disabled />
          </div>
          <div v-for="(provider, i) in services.ai.providers" :key="i" class="services-provider-card">
            <div class="services-provider-title">{{ provider['ui-label'] || provider.driver }}</div>
            <p v-if="provider['ui-description']" class="services-provider-desc">{{ provider['ui-description'] }}</p>
            <div v-for="[label, value] in providerFields(provider)" :key="label" class="services-field">
              <label class="services-field-label">{{ label }}</label>
              <input class="services-field-input" type="text" :value="value" disabled />
            </div>
          </div>
        </div>

        <div v-show="activeTab === 'talk'" class="services-panel">
          <div class="services-provider-card">
            <div class="services-provider-title">Model</div>
            <div class="services-field">
              <label class="services-field-label">Enabled</label>
              <input class="services-field-input" type="text" :value="services.talk.enabled ? 'Yes' : 'No'" disabled />
            </div>
          </div>
          <div v-for="(provider, i) in services.talk.providers" :key="i" class="services-provider-card">
            <div class="services-provider-title">{{ provider['ui-label'] || provider.driver }}</div>
            <p v-if="provider['ui-description']" class="services-provider-desc">{{ provider['ui-description'] }}</p>
            <div v-for="[label, value] in providerFields(provider)" :key="label" class="services-field">
              <label class="services-field-label">{{ label }}</label>
              <input class="services-field-input" type="text" :value="value" disabled />
            </div>
          </div>
        </div>

        <div v-show="activeTab === 'listen'" class="services-panel">
          <div class="services-provider-card">
            <div class="services-provider-title">Model</div>
            <div class="services-field">
              <label class="services-field-label">Enabled</label>
              <input class="services-field-input" type="text" :value="services.listen.enabled ? 'Yes' : 'No'" disabled />
            </div>
          </div>
          <div v-for="(provider, i) in services.listen.providers" :key="i" class="services-provider-card">
            <div class="services-provider-title">{{ provider['ui-label'] || provider.driver }}</div>
            <p v-if="provider['ui-description']" class="services-provider-desc">{{ provider['ui-description'] }}</p>
            <div v-for="[label, value] in providerFields(provider)" :key="label" class="services-field">
              <label class="services-field-label">{{ label }}</label>
              <input class="services-field-input" type="text" :value="value" disabled />
            </div>
          </div>
        </div>

        <div v-show="activeTab === 'database'" class="services-panel">
          <div v-for="[key, value] in Object.entries(services.database)" :key="key" class="services-field">
            <label class="services-field-label">{{ fieldLabel(key) }}</label>
            <input class="services-field-input" type="text" :value="value" disabled />
          </div>

          <div class="services-section">
            <span class="services-section-title">Backup</span>
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
            </div>
          </div>

          <div class="services-section">
            <span class="services-section-title">Danger zone</span>
            <div class="services-actions-row">
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
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #666;
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

.services-provider-card {
  margin: 0.75rem 0;
  padding: 0.75rem 1rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
}

.services-provider-title {
  font-weight: 600;
  font-size: 0.9rem;
  color: #333;
  margin-bottom: 0.4rem;
}

.services-provider-desc {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
  color: #666;
  line-height: 1.4;
}

.services-section {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  margin: 1.25rem 0 0.75rem;
  padding-top: 1rem;
  border-top: 1px solid #eee;
}

.services-section-title {
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: #777;
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
