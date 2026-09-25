<script setup>
import { ref } from 'vue'
import SegmentedControl from '../../../../components/skillkit/SegmentedControl.vue'
import CostsExplorer from './CostsExplorer.vue'

defineProps({
  section: { type: Object, required: true }
})

const TABS = [{ id: 'costs', label: 'Costs' }]
const activeTab = ref(TABS[0].id)
</script>

<template>
  <div class="platform-services">
    <SegmentedControl v-model="activeTab" :options="TABS" class="platform-services-tabs" />
    <CostsExplorer v-if="activeTab === 'costs'" />
  </div>
</template>

<style scoped>
.platform-services { flex: 1; display: flex; flex-direction: column; min-height: 0; gap: 0.75rem; }
.platform-services-tabs { align-self: flex-start; }
</style>
