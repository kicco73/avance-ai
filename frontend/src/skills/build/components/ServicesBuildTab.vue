<script setup>
import ServicesSecretField from '../../../components/settings/ServicesSecretField.vue'
import ServicesFieldList from '../../../components/settings/ServicesFieldList.vue'

defineProps({
  section: { type: Object, required: true }
})

const MASKED_FIELDS = ['token']
</script>

<template>
  <div class="services-panel">
    <ServicesFieldList :section="section" :skip="MASKED_FIELDS" />
    <ServicesSecretField v-for="name in MASKED_FIELDS" :key="name" :name="name" :value="section[name]" />
  </div>
</template>

<style scoped>
.services-panel { max-width: 520px; }
.services-field { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.9rem; }
.services-field-label { font-size: 0.78rem; color: #666; text-transform: uppercase; letter-spacing: 0.03em; }
.services-field-input { padding: 0.45rem 0.6rem; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; color: #333; font-size: 0.9rem; }
</style>
