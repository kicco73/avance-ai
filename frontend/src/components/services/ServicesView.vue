<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppHeader from '../AppHeader.vue'
import DocInfoButton from '../DocInfoButton.vue'
import ExplorerSplitView from '../ExplorerSplitView.vue'
import ProfileMenu from '../ProfileMenu.vue'
import TrendLineChart from './TrendLineChart.vue'
import AiCostsPanel from './AiCostsPanel.vue'
import ServicesProviderCard from '../skillkit/ServicesProviderCard.vue'
import ServicesFieldList from '../skillkit/ServicesFieldList.vue'
import StatusToggleButton from './StatusToggleButton.vue'
import TaskCard from './TaskCard.vue'
import { getAiUsage, getDbUsage, getScheduledTasks, getServicesConfig } from '../../api.js'
import { confirmDialog } from '../../dialogStore.js'
import { modelSelector } from '../../modelSelector.js'
import { servicesTabs, servicesTabActions } from '../../skills/registry.js'
import { fieldLabel } from '../skillkit/serviceFields.js'

defineProps({
  profile: { type: Object, default: null }
})

const emit = defineEmits(['close', 'home', 'profile', 'logout'])


const CORE_TABS = [
  { id: 'ai', label: 'AI' },
  { id: 'chat', label: 'Chat' },
  { id: 'scheduler', label: 'Scheduler', description: 'Deferred work the platform runs on its own clock.' },
  { id: 'database', label: 'Data' }
]

const tabs = computed(() => [...CORE_TABS, ...servicesTabs.value]
  .filter((tab) => tab.id === 'scheduler' || services.value?.[tab.id])
  .map((tab) => ({
    ...tab,
    label: services.value?.[tab.id]?.['ui-label'] || tab.label,
    description: services.value?.[tab.id]?.['ui-description'] || tab.description || ''
  })))

const contributedTabs = computed(() => tabs.value.filter((tab) => tab.component))

const activeDescription = computed(() => tabs.value.find((tab) => tab.id === activeTab.value)?.description || '')


const activeTab = ref(CORE_TABS[0].id)

const CHAT_FIELDS_KEPT_IN_CHAT = ['max-session-duration-in-minutes', 'project-file-cache-bytes']
const chatFieldsMovedToAi = computed(() => Object.keys(services.value?.chat ?? {})
  .filter((key) => !key.startsWith('ui-') && !CHAT_FIELDS_KEPT_IN_CHAT.includes(key)))

const AI_SUBTABS = [
  { id: 'configuration', label: 'Configuration' },
  { id: 'providers', label: 'Providers' },
  { id: 'observability', label: 'Observability' },
  { id: 'costs', label: 'Costs' }
]
const aiSubTab = ref(AI_SUBTABS[0].id)

const DB_SUBTABS = [
  { id: 'configuration', label: 'Configuration' },
  { id: 'observability', label: 'Observability' }
]
const dbSubTab = ref(DB_SUBTABS[0].id)

const services = ref(null)
const loading = ref(true)

const aiUsage = ref({ today: {}, today_cache_read: {}, history: [], provider_changes: [], error_history: [], cache_read_ratio: {} })
const LATENCY_SERIES = [
  { suffix: ':total', label: 'total', values: (entry) => entry.duration },
  { suffix: ':first-chunk', label: 'first chunk', values: (entry) => entry.time_to_first_chunk },
]
const aiLatencyHistory = computed(() => aiUsage.value.history.map((entry) => ({
  timestamp: entry.timestamp,
  values: Object.fromEntries(
    LATENCY_SERIES.flatMap(({ suffix, values }) => Object.entries(values(entry))
      .map(([provider, seconds]) => [`${provider}${suffix}`, seconds]))
  ),
})))
const aiLatencyLabels = computed(() => Object.fromEntries(
  Object.entries(aiProviderLabels.value).flatMap(([provider, label]) =>
    LATENCY_SERIES.map(({ suffix, label: seriesLabel }) => [`${provider}${suffix}`, `${label} (${seriesLabel})`]))
))
const aiProviderChanges = computed(() =>
  aiUsage.value.provider_changes.map((change) => ({
    timestamp: change.timestamp,
    key: `${change.provider_label}:total`,
    label: aiProviderLabels.value[change.provider_label] ?? change.provider_label,
  }))
)
const aiErrorOutcomeLabels = computed(() => Object.fromEntries(
  [...new Set(aiUsage.value.error_history.flatMap((entry) => Object.keys(entry.values)))]
    .map((outcome) => [outcome, outcome.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())])
))

const tokenChartRef = ref(null)
const latencyChartRef = ref(null)
const errorChartRef = ref(null)
const observabilityZoomed = ref(false)
const observabilityCharts = [tokenChartRef, latencyChartRef, errorChartRef]

const observabilityFullRange = computed(() => {
  const times = [...aiUsage.value.history, ...aiUsage.value.error_history]
    .map((entry) => new Date(entry.timestamp).getTime())
  if (times.length < 2) return null
  return { min: Math.min(...times), max: Math.max(...times) }
})

const dbUsage = ref({ request_history: [], history: [], error_history: [] })
function dbQueryLabel(name) {
  return name.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}
function dbLabelsFor(entries) {
  return Object.fromEntries(
    [...new Set(entries.flatMap((entry) => Object.keys(entry.values)))]
      .map((name) => [name, dbQueryLabel(name)])
  )
}
const DB_REQUEST_TOP_N = 5
const DB_OVERALL_KEY = 'overall'
const DB_READ_KEY = 'read'
const DB_WRITE_KEY = 'write'
const dbTopRequestQueryNames = computed(() => {
  const totals = {}
  for (const entry of dbUsage.value.request_history) {
    for (const [name, count] of Object.entries(entry.values)) totals[name] = (totals[name] ?? 0) + count
  }
  return Object.entries(totals).sort(([, a], [, b]) => b - a).slice(0, DB_REQUEST_TOP_N).map(([name]) => name)
})
function dbTopSeries(entry) {
  return Object.fromEntries(dbTopRequestQueryNames.value.filter((name) => name in entry.values).map((name) => [name, entry.values[name]]))
}
const dbTopLabels = computed(() => Object.fromEntries(dbTopRequestQueryNames.value.map((name) => [name, dbQueryLabel(name)])))

const dbRequestChartHistory = computed(() => dbUsage.value.request_history.map((entry) => ({
  timestamp: entry.timestamp,
  values: {
    ...dbTopSeries(entry),
    [DB_OVERALL_KEY]: Object.values(entry.values).reduce((sum, count) => sum + count, 0),
  },
})))
const dbRequestLabels = computed(() => ({ ...dbTopLabels.value, [DB_OVERALL_KEY]: 'Overall' }))

const dbLatencyChartHistory = computed(() => dbUsage.value.history.map((entry) => ({
  timestamp: entry.timestamp,
  values: {
    ...dbTopSeries(entry),
    [DB_OVERALL_KEY]: entry.values[DB_OVERALL_KEY],
    [DB_READ_KEY]: entry.values[DB_READ_KEY],
    [DB_WRITE_KEY]: entry.values[DB_WRITE_KEY],
  },
})))
const dbLatencyLabels = computed(() => ({
  ...dbTopLabels.value, [DB_OVERALL_KEY]: 'Overall', [DB_READ_KEY]: 'Read', [DB_WRITE_KEY]: 'Write',
}))
const dbErrorLabels = computed(() => dbLabelsFor(dbUsage.value.error_history))

const dbRequestChartRef = ref(null)
const dbLatencyChartRef = ref(null)
const dbErrorChartRef = ref(null)
const dbObservabilityZoomed = ref(false)
const dbObservabilityCharts = [dbRequestChartRef, dbLatencyChartRef, dbErrorChartRef]

const dbObservabilityFullRange = computed(() => {
  const times = [...dbUsage.value.request_history, ...dbUsage.value.history, ...dbUsage.value.error_history]
    .map((entry) => new Date(entry.timestamp).getTime())
  if (times.length < 2) return null
  return { min: Math.min(...times), max: Math.max(...times) }
})

const observabilityWindow = ref(null)
let pendingObservabilitySync = null

function onObservabilityRangeChanged(range, sourceRef) {
  observabilityZoomed.value = true
  observabilityWindow.value = range
  const wasScheduled = pendingObservabilitySync !== null
  pendingObservabilitySync = { range, sourceRef }
  if (wasScheduled) return
  requestAnimationFrame(() => {
    const { range: syncedRange, sourceRef: syncedSourceRef } = pendingObservabilitySync
    pendingObservabilitySync = null
    for (const chartRef of observabilityCharts) {
      if (chartRef !== syncedSourceRef) chartRef.value?.setXRange(syncedRange)
    }
  })
}

function resetObservabilityZoom() {
  for (const chartRef of observabilityCharts) {
    if (observabilityFullRange.value) chartRef.value?.setXRange(observabilityFullRange.value)
    else chartRef.value?.resetZoom()
  }
  observabilityWindow.value = null
  observabilityZoomed.value = false
}

const dbObservabilityWindow = ref(null)
let pendingDbObservabilitySync = null

function onDbObservabilityRangeChanged(range, sourceRef) {
  dbObservabilityZoomed.value = true
  dbObservabilityWindow.value = range
  const wasScheduled = pendingDbObservabilitySync !== null
  pendingDbObservabilitySync = { range, sourceRef }
  if (wasScheduled) return
  requestAnimationFrame(() => {
    const { range: syncedRange, sourceRef: syncedSourceRef } = pendingDbObservabilitySync
    pendingDbObservabilitySync = null
    for (const chartRef of dbObservabilityCharts) {
      if (chartRef !== syncedSourceRef) chartRef.value?.setXRange(syncedRange)
    }
  })
}

function resetDbObservabilityZoom() {
  for (const chartRef of dbObservabilityCharts) {
    if (dbObservabilityFullRange.value) chartRef.value?.setXRange(dbObservabilityFullRange.value)
    else chartRef.value?.resetZoom()
  }
  dbObservabilityWindow.value = null
  dbObservabilityZoomed.value = false
}

const AI_USAGE_REFRESH_MS = 60 * 1000

async function refreshAiUsageAndScroll() {
  await loadAiUsage()
  if (!observabilityWindow.value) return
  await nextTick()
  observabilityWindow.value = {
    min: observabilityWindow.value.min + AI_USAGE_REFRESH_MS,
    max: observabilityWindow.value.max + AI_USAGE_REFRESH_MS,
  }
  for (const chartRef of observabilityCharts) chartRef.value?.setXRange(observabilityWindow.value)
}

async function refreshDbUsageAndScroll() {
  await loadDbUsage()
  if (!dbObservabilityWindow.value) return
  await nextTick()
  dbObservabilityWindow.value = {
    min: dbObservabilityWindow.value.min + AI_USAGE_REFRESH_MS,
    max: dbObservabilityWindow.value.max + AI_USAGE_REFRESH_MS,
  }
  for (const chartRef of dbObservabilityCharts) chartRef.value?.setXRange(dbObservabilityWindow.value)
}

let aiUsageRefreshTimer = null
let dbUsageRefreshTimer = null
onBeforeUnmount(() => {
  clearInterval(aiUsageRefreshTimer)
  clearInterval(dbUsageRefreshTimer)
})

const TASK_STATUSES = ['pending', 'dispatched', 'done', 'failed', 'canceled']
const taskStatus = ref(TASK_STATUSES[0])
const taskOrder = ref('asc')
const tasks = ref([])
const tasksLoading = ref(true)

function providerLabel(provider) {
  return `${provider.driver}/${provider.model}`
}

const aiProviderLabels = computed(() => {
  if (!services.value) return {}
  return Object.fromEntries(liveProviders.value.map((p) => [providerLabel(p), p['ui-label'] || p.driver]))
})

async function load() {
  loading.value = true
  try {
    services.value = await getServicesConfig()
  } catch {
  } finally {
    loading.value = false
  }
}

async function loadAiUsage() {
  try {
    aiUsage.value = await getAiUsage()
  } catch {
  }
}

async function loadDbUsage() {
  try {
    dbUsage.value = await getDbUsage()
  } catch {
  }
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    tasks.value = (await getScheduledTasks(taskStatus.value, taskOrder.value)).tasks
  } catch {
  } finally {
    tasksLoading.value = false
  }
}

watch([taskStatus, taskOrder], loadTasks)


onMounted(() => {
  load()
  loadAiUsage()
  loadDbUsage()
  loadTasks()
  aiUsageRefreshTimer = setInterval(refreshAiUsageAndScroll, AI_USAGE_REFRESH_MS)
  dbUsageRefreshTimer = setInterval(refreshDbUsageAndScroll, AI_USAGE_REFRESH_MS)
})

const NO_FALLBACK_WARNING =
  'If the current provider fails or runs out of tokens, live chat will no longer automatically fall back to the next one and the service will stay broken. Continue?'

async function selectModelWithConfirm(index) {
  if (modelSelector().selectionLoading.value) return
  const alreadySelected = index === (modelSelector().auto.value ? null : modelSelector().currentIndex.value)
  if (alreadySelected) return
  const ok = index == null
    ? await confirmDialog({
        title: 'Enable auto-live cascading',
        body: `Switch the live chat provider to "${modelSelector().autoLabel ?? 'Auto'}"?`,
        okLabel: 'Switch'
      })
    : await confirmDialog({
        title: 'Change provider',
        body: NO_FALLBACK_WARNING,
        okLabel: 'Switch',
        danger: true
      })
  if (!ok) return
  await modelSelector().select(index)
}

async function toggleAutoLive() {
  if (modelSelector().selectionLoading.value) return
  if (!modelSelector().auto.value) {
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
  await modelSelector().select(modelSelector().currentIndex.value)
}

const liveProviders = computed(() => {
  if (!services.value) return []
  return services.value.ai.providers.filter(isProviderLive)
})

function isProviderLive(provider) {
  return !Array.isArray(provider.modes) || provider.modes.includes('live')
}

function isProviderActive(index) {
  return modelSelector().currentIndex.value === index
}

function providerStatusTitle(index) {
  return isProviderActive(index) ? 'Active provider' : 'Set as the active provider'
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
        <DocInfoButton doc-name="skills" title="Skills" />
      </template>
      <template #right>
        <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
      </template>
    </AppHeader>

    <div class="services-workspace">
    <ExplorerSplitView :items="tabs" :active-id="activeTab" title="Services" @select="activeTab = $event">
      <template #item-icon="{ item }">
        <svg v-if="item.id === 'ai'" class="services-tab-ai-icon" viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
          <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
        </svg>
      </template>

      <div class="services-body">
        <p v-if="activeDescription && activeTab !== 'ai' && activeTab !== 'database'" class="services-tab-description">{{ activeDescription }}</p>
        <p v-if="loading" class="services-status">Loading…</p>
        <template v-else-if="services">
          <div v-show="activeTab === 'chat'" class="services-panel">
            <ServicesFieldList :section="services.chat" :skip="chatFieldsMovedToAi" />
          </div>

          <div v-show="activeTab === 'scheduler'" class="services-panel">
            <div class="services-scheduler-toolbar">
              <div class="services-segmented">
                <button
                  v-for="status in TASK_STATUSES"
                  :key="status"
                  type="button"
                  class="services-segment-btn"
                  :class="{ 'services-segment-active': taskStatus === status }"
                  @click="taskStatus = status"
                >{{ status }}</button>
              </div>
              <button
                type="button"
                class="services-sort-btn"
                :title="taskOrder === 'asc' ? 'Sort ascending' : 'Sort descending'"
                @click="taskOrder = taskOrder === 'asc' ? 'desc' : 'asc'"
              >Time {{ taskOrder === 'asc' ? '↓' : '↑' }}</button>
            </div>
            <p v-if="tasksLoading" class="services-status">Loading…</p>
            <p v-else-if="!tasks.length" class="services-status">No {{ taskStatus }} tasks.</p>
            <TaskCard v-for="task in tasks" :key="task.key" :task="task" />
          </div>

          <div v-show="activeTab === 'ai'" class="services-panel">
            <div class="services-ai-subtabs">
              <button
                v-for="subtab in AI_SUBTABS"
                :key="subtab.id"
                type="button"
                class="services-ai-subtab-btn"
                :class="{ 'services-ai-subtab-btn-active': aiSubTab === subtab.id }"
                @click="aiSubTab = subtab.id"
              >{{ subtab.label }}</button>
            </div>

            <div v-show="aiSubTab === 'configuration'" class="services-ai-subpanel">
              <p v-if="activeDescription" class="services-tab-description">{{ activeDescription }}</p>
              <div class="services-field">
                <label class="services-field-label">Max output tokens</label>
                <input class="services-field-input" type="text" :value="services.ai['max-output-tokens']" disabled />
              </div>
              <ServicesFieldList :section="services.chat" :skip="CHAT_FIELDS_KEPT_IN_CHAT" />
            </div>

            <div v-show="aiSubTab === 'providers'" class="services-ai-subpanel">
              <label class="services-checkbox-field services-checkbox-field-active">
                <input type="checkbox" :checked="modelSelector().auto.value" @click.prevent="toggleAutoLive" />
                Auto-live cascading enabled
              </label>
              <div v-for="(provider, i) in liveProviders" :key="i" class="services-provider-row">
                <ServicesProviderCard
                  class="services-provider-row-card"
                  :provider="provider"
                  :usage-today="aiUsage.today[providerLabel(provider)] ?? null"
                  :usage-today-cache-read="aiUsage.today_cache_read[providerLabel(provider)] ?? null"
                  :cache-read-ratio="aiUsage.cache_read_ratio[providerLabel(provider)] ?? null"
                />
                <StatusToggleButton
                  :status="isProviderActive(i) ? 'running' : 'manually_paused'"
                  :disabled="isProviderActive(i) || modelSelector().selectionLoading.value"
                  :title="providerStatusTitle(i)"
                  @click="selectModelWithConfirm(i)"
                />
              </div>
            </div>

            <div v-show="aiSubTab === 'costs'" class="services-ai-subpanel">
              <AiCostsPanel :active="activeTab === 'ai' && aiSubTab === 'costs'" :providers="services.ai.providers" />
            </div>

            <div v-show="aiSubTab === 'observability'" class="services-ai-subpanel">
              <div class="services-observability-toolbar">
                <button
                  :disabled="!observabilityZoomed"
                  class="services-observability-reset-zoom-btn"
                  @click="resetObservabilityZoom"
                >
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                    <path d="M12 5V2L8 6l4 4V7c3.31 0 6 2.69 6 6 0 2.97-2.17 5.43-5 5.91v2.02c3.95-.49 7-3.85 7-7.93 0-4.42-3.58-8-8-8zm-6 8c0-1.65.67-3.15 1.76-4.24L6.34 7.34C4.9 8.79 4 10.79 4 13c0 4.08 3.05 7.44 7 7.93v-2.02c-2.83-.48-5-2.94-5-5.91z" />
                  </svg>
                  Reset
                </button>
              </div>
              <div v-if="liveProviders.length && aiUsage.history.length >= 2" class="services-ai-usage-chart">
                <TrendLineChart
                  ref="tokenChartRef"
                  title="Token usage"
                  :history="aiUsage.history"
                  :provider-labels="aiProviderLabels"
                  @range-changed="onObservabilityRangeChanged($event, tokenChartRef)"
                >
                  <template #value="{ value }">{{ Math.round(value).toLocaleString() }} tokens</template>
                </TrendLineChart>
              </div>
              <div v-if="liveProviders.length && aiUsage.history.length >= 2" class="services-ai-usage-chart">
                <TrendLineChart
                  ref="latencyChartRef"
                  title="Latency"
                  :history="aiLatencyHistory"
                  :markers="aiProviderChanges"
                  :provider-labels="aiLatencyLabels"
                  @range-changed="onObservabilityRangeChanged($event, latencyChartRef)"
                >
                  <template #value="{ value }">{{ value.toFixed(2) }} s</template>
                </TrendLineChart>
              </div>
              <div v-if="liveProviders.length && aiUsage.error_history.length >= 2" class="services-ai-usage-chart">
                <TrendLineChart
                  ref="errorChartRef"
                  title="Errors"
                  :history="aiUsage.error_history"
                  :provider-labels="aiErrorOutcomeLabels"
                  @range-changed="onObservabilityRangeChanged($event, errorChartRef)"
                >
                  <template #value="{ value }">{{ value }} {{ value === 1 ? 'error' : 'errors' }}</template>
                </TrendLineChart>
              </div>
            </div>
          </div>

          <div v-show="activeTab === 'database'" class="services-panel">
            <div class="services-ai-subtabs">
              <button
                v-for="subtab in DB_SUBTABS"
                :key="subtab.id"
                type="button"
                class="services-ai-subtab-btn"
                :class="{ 'services-ai-subtab-btn-active': dbSubTab === subtab.id }"
                @click="dbSubTab = subtab.id"
              >{{ subtab.label }}</button>
            </div>

            <div v-show="dbSubTab === 'configuration'" class="services-ai-subpanel">
              <p v-if="activeDescription" class="services-tab-description">{{ activeDescription }}</p>
              <ServicesFieldList :section="services.database" />

              <component
                v-for="entry in servicesTabActions.filter((e) => e.tab === 'database')"
                :key="entry.id"
                :is="entry.component"
              />
            </div>

            <div v-show="dbSubTab === 'observability'" class="services-ai-subpanel">
              <div class="services-observability-toolbar">
                <button
                  :disabled="!dbObservabilityZoomed"
                  class="services-observability-reset-zoom-btn"
                  @click="resetDbObservabilityZoom"
                >
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                    <path d="M12 5V2L8 6l4 4V7c3.31 0 6 2.69 6 6 0 2.97-2.17 5.43-5 5.91v2.02c3.95-.49 7-3.85 7-7.93 0-4.42-3.58-8-8-8zm-6 8c0-1.65.67-3.15 1.76-4.24L6.34 7.34C4.9 8.79 4 10.79 4 13c0 4.08 3.05 7.44 7 7.93v-2.02c-2.83-.48-5-2.94-5-5.91z" />
                  </svg>
                  Reset
                </button>
              </div>
              <div class="services-ai-usage-chart">
                <TrendLineChart
                  ref="dbRequestChartRef"
                  title="Requests"
                  :history="dbRequestChartHistory"
                  :provider-labels="dbRequestLabels"
                  :bold-keys="[DB_OVERALL_KEY]"
                  :series-colors="{ [DB_OVERALL_KEY]: '#c0392b' }"
                  @range-changed="onDbObservabilityRangeChanged($event, dbRequestChartRef)"
                >
                  <template #value="{ value }">{{ value }} {{ value === 1 ? 'request' : 'requests' }}</template>
                </TrendLineChart>
              </div>
              <div class="services-ai-usage-chart">
                <TrendLineChart
                  ref="dbLatencyChartRef"
                  title="Latency"
                  :history="dbLatencyChartHistory"
                  :provider-labels="dbLatencyLabels"
                  :bold-keys="[DB_OVERALL_KEY]"
                  :series-colors="{ [DB_OVERALL_KEY]: '#c0392b' }"
                  @range-changed="onDbObservabilityRangeChanged($event, dbLatencyChartRef)"
                >
                  <template #value="{ value }">{{ value.toFixed(3) }} s</template>
                </TrendLineChart>
              </div>
              <div class="services-ai-usage-chart">
                <TrendLineChart
                  ref="dbErrorChartRef"
                  title="Errors"
                  :history="dbUsage.error_history"
                  :provider-labels="dbErrorLabels"
                  @range-changed="onDbObservabilityRangeChanged($event, dbErrorChartRef)"
                >
                  <template #value="{ value }">{{ value }} {{ value === 1 ? 'error' : 'errors' }}</template>
                </TrendLineChart>
              </div>
            </div>
          </div>

          <component
            v-for="tab in contributedTabs"
            :key="tab.id"
            :is="tab.component"
            v-show="activeTab === tab.id"
            :section="services[tab.id]"
          />
        </template>
      </div>
    </ExplorerSplitView>
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
  margin-right: 0.5rem;
}

.services-tab-ai-icon {
  flex-shrink: 0;
  color: #8b5cf6;
}

.services-workspace {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
  padding-bottom: calc(1rem + var(--safe-area-bottom));
}

.services-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1.25rem;
}

.services-tab-description {
  margin: 0 0 1rem;
  font-size: 0.82rem;
  color: #777;
  line-height: 1.4;
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

.services-scheduler-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin: 0 0 0.9rem;
}

.services-segmented {
  display: inline-flex;
  padding: 0.2rem;
  border-radius: 8px;
  background: #f0f0f2;
}

.services-segment-btn {
  padding: 0.35rem 0.8rem;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  font-size: 0.8rem;
  text-transform: capitalize;
  cursor: pointer;
}

.services-segment-active {
  background: white;
  color: #2c4d7a;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
}

.services-ai-subtabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid #ddd;
  margin: 0 0 0.9rem;
}

.services-ai-subtab-btn {
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  color: #666;
}

.services-ai-subtab-btn:hover {
  color: #333;
}

.services-ai-subtab-btn-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
}

.services-ai-subpanel {
  display: flex;
  flex-direction: column;
}

.services-sort-btn {
  flex-shrink: 0;
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  border: 1px solid #ddd;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.8rem;
}

.services-sort-btn:hover {
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

.services-checkbox-field-active {
  cursor: pointer;
}

.services-checkbox-field-active input[type="checkbox"] {
  cursor: pointer;
}

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

.services-ai-usage-chart {
  width: 100%;
  height: 200px;
  max-height: 200px;
  flex-shrink: 0;
  margin: 0.75rem 0;
}

.services-observability-toolbar {
  display: flex;
  justify-content: flex-end;
  min-height: 1.6rem;
}

.services-observability-reset-zoom-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.72rem;
  cursor: pointer;
}

.services-observability-reset-zoom-btn:hover {
  background: #4a6fa5;
  color: white;
}

.services-observability-reset-zoom-btn:disabled {
  border-color: #ccc;
  color: #aaa;
  cursor: default;
}

.services-observability-reset-zoom-btn:disabled:hover {
  background: white;
  color: #aaa;
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
