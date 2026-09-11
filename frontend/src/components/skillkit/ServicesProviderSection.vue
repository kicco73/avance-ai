<script setup>
import ServicesProviderCard from './ServicesProviderCard.vue'

defineProps({
  section: { type: Object, required: true }
})
</script>

<template>
  <div class="services-panel">
    <label class="services-checkbox-field">
      <input type="checkbox" :checked="section.enabled" disabled />
      Service enabled
    </label>
    <ServicesProviderCard v-for="(provider, i) in section.providers" :key="i" :provider="provider" />
  </div>
</template>

<style scoped>
.services-panel { max-width: 520px; }
.services-checkbox-field { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.9rem; font-size: 0.9rem; color: #333; }
</style>
