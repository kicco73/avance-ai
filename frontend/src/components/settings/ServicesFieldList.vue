<script setup>
import { computed } from 'vue'
import { fieldLabel } from './serviceFields.js'

const props = defineProps({
  section: { type: Object, default: () => ({}) },
  skip: { type: Array, default: () => [] }
})

const fields = computed(() => Object.entries(props.section)
  .filter(([key]) => !key.startsWith('ui-') && !props.skip.includes(key)))
</script>

<template>
  <div v-for="[name, value] in fields" :key="name" class="services-field">
    <label class="services-field-label">{{ fieldLabel(name) }}</label>
    <input class="services-field-input" type="text" :value="value" disabled />
  </div>
</template>

<style scoped>
.services-field { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.9rem; }
.services-field-label { font-size: 0.78rem; color: #666; text-transform: uppercase; letter-spacing: 0.03em; }
.services-field-input { padding: 0.45rem 0.6rem; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; color: #333; font-size: 0.9rem; }
</style>
