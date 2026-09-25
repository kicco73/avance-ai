<script setup>
import { computed, onMounted, ref } from 'vue'
import ExplorerSplitView from '../../../../components/ExplorerSplitView.vue'
import TrendLineChart from '../../../../components/services/TrendLineChart.vue'
import CostDistributionChart from './CostDistributionChart.vue'
import { getCostApps, getCostSeries, getCostSessions, getCostTurns, getCostUsers } from '../../api.js'

const INDENT_PX = 12

const apps = ref([])
const childrenById = ref({})
const expanded = ref(new Set())
const nodesById = ref({})
const selectedId = ref(null)
const series = ref(null)
const turns = ref(null)
const failed = ref(false)

function remember(nodes) {
  nodesById.value = { ...nodesById.value, ...Object.fromEntries(nodes.map((node) => [node.id, node])) }
  return nodes
}

const APP_BRANCHES = [
  { scope: 'test', label: 'Test' },
  { scope: 'preview', label: 'Preview' },
  { scope: 'run', label: 'Run' }
]

function appNode(app) {
  return { id: `app:${app.id}`, kind: 'app', label: app.label, depth: 0, scope: { projectId: app.id, scope: 'app' }, leaf: false }
}

function branchNode(parent, branch) {
  return {
    id: `${parent.id}/${branch.scope}`, kind: 'branch', label: branch.label, depth: 1,
    scope: { projectId: parent.scope.projectId, scope: branch.scope }, leaf: true
  }
}

function usersNode(parent) {
  return {
    id: `${parent.id}/users`, kind: 'users', label: 'Users', depth: 1,
    scope: { projectId: parent.scope.projectId, scope: 'users' }, leaf: false
  }
}

function userNode(parent, user) {
  return {
    id: `${parent.id}/user:${user.id}`, kind: 'user', label: user.label, depth: 2,
    scope: { ...parent.scope, username: user.id }, leaf: false
  }
}

function sessionNode(parent, costEntry) {
  return {
    id: `${parent.id}/session:${costEntry.id}`, kind: 'session', label: costEntry.label, depth: 3,
    scope: { ...parent.scope, sessionId: costEntry.id }, leaf: true
  }
}

const WITH_TURN_DISTRIBUTION = new Set(['app', 'users'])

async function loadTurns(node) {
  turns.value = null
  if (!WITH_TURN_DISTRIBUTION.has(node.kind)) return
  try {
    const loaded = await getCostTurns({ projectId: node.scope.projectId, scope: node.scope.scope })
    if (selectedId.value === node.id) turns.value = { nodeId: node.id, ...loaded }
  } catch {
    turns.value = null
  }
}

const CHILDREN_OF = {
  app: async (node) => [...APP_BRANCHES.map((branch) => branchNode(node, branch)), usersNode(node)],
  users: async (node) => (await getCostUsers(node.scope.projectId)).users.map((user) => userNode(node, user)),
  user: async (node) => (await getCostSessions(node.scope.projectId, node.scope.username)).sessions
    .map((entry) => sessionNode(node, entry))
}

async function loadChildren(node) {
  const children = await CHILDREN_OF[node.kind](node)
  childrenById.value = { ...childrenById.value, [node.id]: remember(children) }
}

const rows = computed(() => {
  const visible = []
  const walk = (nodes) => {
    for (const node of nodes) {
      visible.push(node)
      if (expanded.value.has(node.id)) walk(childrenById.value[node.id] ?? [])
    }
  }
  walk(apps.value)
  return visible
})

async function select(id) {
  const node = nodesById.value[id]
  const wasSelected = selectedId.value === id
  selectedId.value = id
  failed.value = false
  if (!node.leaf) {
    const next = new Set(expanded.value)
    if (wasSelected && next.has(id)) next.delete(id)
    else next.add(id)
    expanded.value = next
    if (!(id in childrenById.value)) loadChildren(node)
  }
  try {
    loadTurns(node)
    const loaded = await getCostSeries(node.scope)
    if (selectedId.value === id) series.value = { nodeId: id, ...loaded }
  } catch {
    if (selectedId.value === id) failed.value = true
  }
}

const selectedNode = computed(() => nodesById.value[selectedId.value] ?? null)

const money = computed(() => new Intl.NumberFormat(undefined, {
  style: 'currency', currency: series.value?.currency ?? 'EUR', maximumFractionDigits: 4
}))

const SUMMARY = [
  { key: 'last_24_hours', label: 'Last 24 hours' },
  { key: 'last_7_days', label: 'Last 7 days' },
  { key: 'mean_per_day', label: 'Mean per day' },
  { key: 'mean_per_week', label: 'Mean per week' }
]

onMounted(async () => {
  try {
    apps.value = remember((await getCostApps()).apps.map(appNode))
  } catch {
    failed.value = true
  }
})
</script>

<template>
  <ExplorerSplitView :items="rows" :active-id="selectedId" title="Apps" @select="select">
    <template #item-icon="{ item }">
      <span class="costs-indent" :style="{ width: `${item.depth * INDENT_PX}px` }"></span>
      <span class="costs-chevron">{{ item.leaf ? '' : (expanded.has(item.id) ? '▾' : '▸') }}</span>
    </template>

    <div class="costs-panel">
      <p v-if="failed" class="costs-note">Costs could not be loaded.</p>
      <p v-else-if="!selectedNode" class="costs-note">Select an app, or one of its branches, to see what it cost.</p>
      <template v-else-if="series && series.nodeId === selectedId">
        <h3 class="costs-title">{{ selectedNode.label }}</h3>
        <dl class="costs-summary">
          <div v-for="entry in SUMMARY" :key="entry.key" class="costs-summary-item">
            <dt>{{ entry.label }}</dt>
            <dd>{{ money.format(series.summary[entry.key]) }}</dd>
          </div>
        </dl>
        <div v-if="series.history.length >= 2" class="costs-chart">
          <TrendLineChart title="Cost per day" :history="series.history">
            <template #value="{ value }">{{ money.format(value) }}</template>
          </TrendLineChart>
        </div>
        <p v-else class="costs-note">Not enough days of usage yet to draw a trend.</p>
        <template v-if="turns && turns.nodeId === selectedId">
          <h4 class="costs-subtitle">Cost per turn</h4>
          <p v-if="!turns.turns" class="costs-note">No turns recorded in this window yet.</p>
          <template v-else>
            <dl class="costs-summary">
              <div class="costs-summary-item"><dt>Turns</dt><dd>{{ turns.turns }}</dd></div>
              <div class="costs-summary-item"><dt>Mean</dt><dd>{{ money.format(turns.mean) }}</dd></div>
              <div class="costs-summary-item"><dt>Median</dt><dd>{{ money.format(turns.median) }}</dd></div>
              <div v-if="turns.extra_per_turn" class="costs-summary-item">
                <dt>Extras per turn</dt><dd>{{ money.format(turns.extra_per_turn) }}</dd>
              </div>
            </dl>
            <div class="costs-chart">
              <CostDistributionChart :bins="turns.bins" :currency="turns.currency" />
            </div>
          </template>
        </template>
        <p v-if="series.unpriced_providers.length" class="costs-note">
          No price set, counted as zero: {{ series.unpriced_providers.join(', ') }}
        </p>
      </template>
    </div>
  </ExplorerSplitView>
</template>

<style scoped>
.costs-indent { display: inline-block; flex-shrink: 0; }
.costs-chevron { display: inline-block; width: 0.8rem; flex-shrink: 0; color: #888; }
.costs-panel { padding: 0.75rem 1rem; overflow-y: auto; }
.costs-title { margin: 0 0 0.75rem; font-size: 0.95rem; color: #333; }
.costs-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.5rem; margin: 0; }
.costs-summary-item { border: 1px solid #eee; border-radius: 6px; padding: 0.4rem 0.6rem; }
.costs-summary-item dt { font-size: 0.72rem; color: #777; }
.costs-summary-item dd { margin: 0.15rem 0 0; font-size: 0.95rem; font-weight: 600; color: #333; }
.costs-chart { width: 100%; height: 200px; max-height: 200px; margin: 0.75rem 0; }
.costs-subtitle { margin: 1rem 0 0.5rem; font-size: 0.85rem; color: #555; }
.costs-note { margin: 0.5rem 0; font-size: 0.82rem; color: #777; line-height: 1.4; }
</style>
