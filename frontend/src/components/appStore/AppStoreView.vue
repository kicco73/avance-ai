<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppHeader from '../AppHeader.vue'
import ProfileMenu from '../ProfileMenu.vue'
import AppDetailPanel from './AppDetailPanel.vue'
import CustomerAppDetailPanel from './CustomerAppDetailPanel.vue'
import { getAppStoreApps, appStoreFileContentUrl } from '../../api.js'
import avanceLogoUrl from '../../assets/avance-logo.png'
import avanceLogoLargeUrl from '../../assets/avance-logo-large.png'

const props = defineProps({
  standalone: { type: Boolean, default: false },
  profile: { type: Object, default: null },
  title: { type: String, default: 'Store' },
  showLogo: { type: Boolean, default: false },
  subscribedOnly: { type: Boolean, default: false },
  showFreeBadge: { type: Boolean, default: true },
  hideInstallActions: { type: Boolean, default: false },
  tryButtonLabel: { type: String, default: 'Try me!' },
  timedSession: { type: Boolean, default: true },
  showStoreButton: { type: Boolean, default: false },
  showUninstallMenu: { type: Boolean, default: false }
})

const emit = defineEmits(['close', 'open', 'open-store', 'home', 'profile', 'logout'])

const apps = ref([])
const loading = ref(true)
const selectedId = ref(null)
const iconFailedById = ref({})
const searchQuery = ref('')

const visibleApps = computed(() => props.subscribedOnly ? apps.value.filter((app) => app.installed) : apps.value)
const selectedApp = computed(() => apps.value.find((app) => app.id === selectedId.value) ?? null)

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

function selectApp(id) {
  selectedId.value = id
}

function deselectApp() {
  selectedId.value = null
}

watch(() => selectedApp.value?.installed, (installed, wasInstalled) => {
  if (props.subscribedOnly && wasInstalled && installed === false) {
    selectedId.value = visibleApps.value.length ? visibleApps.value[0].id : null
  }
})

async function load() {
  loading.value = true
  try {
    apps.value = (await getAppStoreApps(searchQuery.value)).apps
    if (selectedId.value == null && visibleApps.value.length) selectApp(visibleApps.value[0].id)
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

let searchDebounceHandle = null
watch(searchQuery, () => {
  clearTimeout(searchDebounceHandle)
  searchDebounceHandle = setTimeout(load, 300)
})

function handleDetailOpen(id) {
  emit('open', id)
}

onMounted(load)

onBeforeUnmount(() => clearTimeout(searchDebounceHandle))

defineExpose({ refresh: load })
</script>

<template>
  <div class="app-store-overlay" :class="{ 'app-store-detail-active': !!selectedApp }">
    <AppHeader>
      <template #left>
        <button v-if="!standalone" type="button" class="app-header-icon-btn app-store-back-exit-btn" title="Back" @click="emit('close')">«</button>
        <button type="button" class="app-header-icon-btn app-store-back-list-btn" title="Back to list" @click="deselectApp">«</button>
        <button v-if="showStoreButton" type="button" class="app-store-open-store-btn" title="App store" @click="emit('open-store')">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M20 4H4v2h16V4zM4 20h4v-6h8v6h4v-8H4v8zm16-10l-.67-3.35a2.011 2.011 0 0 0-1.96-1.65H6.63c-.96 0-1.79.68-1.96 1.65L4 10v1c0 1.1.9 2 2 2s2-.9 2-2c0 1.1.9 2 2 2s2-.9 2-2c0 1.1.9 2 2 2s2-.9 2-2c0 1.1.9 2 2 2s2-.9 2-2v-1z" />
          </svg>
          <span>Store</span>
        </button>
      </template>
      <template #center>
        <img v-if="showLogo" :src="avanceLogoLargeUrl" alt="Avance" class="app-store-header-logo" />
        <h2 v-else class="app-header-title app-store-header-title">{{ title }}</h2>
      </template>
      <template #right>
        <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
      </template>
    </AppHeader>

    <div class="app-store-body">
      <div class="app-store-list">
        <input
          v-model="searchQuery"
          type="search"
          class="app-store-search"
          placeholder="Search apps..."
        />
        <div class="app-store-card-list">
          <p v-if="loading" class="app-store-status">Loading…</p>
          <p v-else-if="!visibleApps.length" class="app-store-status">No apps found.</p>
          <button
            v-for="app in visibleApps"
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
      </div>

      <div class="app-store-preview">
        <CustomerAppDetailPanel
          v-if="selectedApp && subscribedOnly"
          :key="selectedApp.id"
          :app="selectedApp"
          @open="handleDetailOpen"
        />
        <AppDetailPanel
          v-else-if="selectedApp"
          :key="selectedApp.id"
          :app="selectedApp"
          :show-free-badge="showFreeBadge"
          :hide-install-actions="hideInstallActions"
          :try-button-label="tryButtonLabel"
          :timed-session="timedSession"
          :show-uninstall-menu="showUninstallMenu"
          @open="handleDetailOpen"
        />
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

.app-store-header-logo {
  height: 1.6rem;
  width: auto;
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
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.app-store-search {
  flex-shrink: 0;
  box-sizing: border-box;
  width: 100%;
  padding: 0.5rem 0.7rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 0.85rem;
}

.app-store-card-list {
  flex: 1;
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

.app-store-status {
  color: #888;
  font-size: 0.9rem;
}

.app-store-back-list-btn {
  display: none;
}

.app-store-open-store-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.app-store-open-store-btn:hover {
  background: #4a6fa5;
  color: white;
}

@media (max-width: 640px) {
  .app-store-body {
    position: relative;
    padding: 0;
    gap: 0;
    overflow: hidden;
  }

  .app-store-list,
  .app-store-preview {
    position: absolute;
    inset: 0;
    width: 100%;
    max-width: none;
    padding: 1rem;
    box-sizing: border-box;
    transition: transform 0.3s ease;
  }

  .app-store-list {
    transform: translateX(0);
  }

  .app-store-preview {
    transform: translateX(100%);
  }

  .app-store-detail-active .app-store-list {
    transform: translateX(-100%);
  }

  .app-store-detail-active .app-store-preview {
    transform: translateX(0);
  }

  .app-store-detail-active .app-store-back-exit-btn {
    display: none;
  }

  .app-store-detail-active .app-store-back-list-btn {
    display: inline-flex;
  }
}
</style>
