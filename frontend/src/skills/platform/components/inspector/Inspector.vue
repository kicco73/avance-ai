<script setup>
import { reactive, ref, watch } from 'vue'

const props = defineProps({
  tabs: { type: Array, required: true },
  activeTab: { type: String, default: null },
  collapsed: { type: Boolean, default: false }
})

const emit = defineEmits(['update:active-tab', 'update:collapsed'])

const registry = reactive({})

function registerTab(id) {
  return (instance) => {
    if (instance) registry[id] = instance
    else delete registry[id]
  }
}

function isValidTab(id) {
  return props.tabs.some((tab) => tab.id === id)
}

const internalActive = ref(isValidTab(props.activeTab) ? props.activeTab : (props.tabs[0]?.id ?? null))

function setActiveTab(id) {
  internalActive.value = id
  emit('update:active-tab', id)
  registry[id]?.refresh?.(true)
}

watch(
  () => props.activeTab,
  (value) => {
    if (value != null && value !== internalActive.value && isValidTab(value)) {
      setActiveTab(value)
    }
  }
)

watch(
  () => props.tabs,
  (tabs) => {
    if (!tabs.some((tab) => tab.id === internalActive.value)) {
      setActiveTab(tabs[0]?.id ?? null)
    }
  },
  { deep: true, immediate: true }
)

async function refresh() {
  await Promise.all(
    Object.entries(registry).map(([id, instance]) => instance.refresh?.(id === internalActive.value))
  )
}

function resize() {
  Object.values(registry).forEach((instance) => instance.resize?.())
}

defineExpose({ refresh, resize })
</script>

<template>
  <div class="inspector-header">
    <span v-show="!collapsed" class="inspector-title">Inspector</span>
    <button
      class="collapse-toggle-btn"
      :title="collapsed ? 'Expand inspector' : 'Collapse inspector'"
      @click="emit('update:collapsed', !collapsed)"
    >{{ collapsed ? '◂' : '▸' }}</button>
  </div>

  <div v-show="!collapsed" class="inspector-tabs">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      class="inspector-tab-btn"
      :class="{ 'inspector-tab-btn-active': internalActive === tab.id }"
      @click="setActiveTab(tab.id)"
    >{{ tab.label }}</button>
  </div>

  <div v-show="!collapsed" class="inspector-body">
    <div v-for="tab in tabs" :key="tab.id" v-show="internalActive === tab.id" class="inspector-tab-panel">
      <slot :name="`tab-${tab.id}`" :register-tab="registerTab" />
    </div>
  </div>
</template>

<style scoped>
.inspector-header { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; background: #f5f5f7; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.inspector-title { font-size: 0.8rem; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
.inspector-tabs { display: flex; gap: 0.25rem; padding: 0.5rem 1rem 0; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.inspector-tab-btn { padding: 0.45rem 0.9rem; border: none; border-bottom: 2px solid transparent; border-radius: 0; background: none; cursor: pointer; font-size: 0.82rem; color: #666; }
.inspector-tab-btn:hover { color: #333; }
.inspector-tab-btn-active { color: #2c4d7a; font-weight: 600; border-bottom-color: #4a6fa5; }
.inspector-body { flex: 1; display: flex; flex-direction: column; min-height: 0; padding: 1rem; }
.inspector-tab-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.collapse-toggle-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 0.9rem; }
.collapse-toggle-btn:hover { background: #eee; }
</style>
