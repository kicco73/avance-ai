<script setup>
import { computed, ref, watch } from 'vue'
import TrendLineChart from './TrendLineChart.vue'
import { getAiCosts } from '../../api.js'

const props = defineProps({
  active: { type: Boolean, default: false },
  providers: { type: Array, default: () => [] }
})

const costs = ref({ currency: 'EUR', history: [], unpriced_providers: [] })
const loading = ref(false)
const failed = ref(false)

const providerLabels = computed(() => Object.fromEntries(
  props.providers.map((p) => [`${p.driver}/${p.model}`, p['ui-label'] || p.driver])
))

const money = computed(() => new Intl.NumberFormat(undefined, {
  style: 'currency', currency: costs.value.currency, maximumFractionDigits: 4
}))

async function load() {
  loading.value = true
  failed.value = false
  try {
    costs.value = await getAiCosts()
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

watch(() => props.active, (active) => { if (active) load() }, { immediate: true })
</script>

<template>
  <div class="ai-costs">
    <p v-if="failed" class="ai-costs-note">Costs could not be loaded.</p>
    <p v-else-if="!loading && costs.history.length < 2" class="ai-costs-note">
      Not enough days of usage yet to draw a trend.
    </p>
    <div v-if="costs.history.length >= 2" class="ai-costs-chart">
      <TrendLineChart title="Cost per day" :history="costs.history" :provider-labels="providerLabels">
        <template #value="{ value }">{{ money.format(value) }}</template>
      </TrendLineChart>
    </div>
    <p v-if="costs.unpriced_providers.length" class="ai-costs-note">
      Not priced, no longer configured: {{ costs.unpriced_providers.join(', ') }}
    </p>
  </div>
</template>

<style scoped>
.ai-costs {
  display: flex;
  flex-direction: column;
}

.ai-costs-chart {
  width: 100%;
  height: 200px;
  max-height: 200px;
  flex-shrink: 0;
  margin: 0.75rem 0;
}

.ai-costs-note {
  margin: 0.5rem 0;
  font-size: 0.82rem;
  color: #777;
  line-height: 1.4;
}
</style>
