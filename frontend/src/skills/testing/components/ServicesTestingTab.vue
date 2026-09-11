<script setup>
import ServicesFieldList from '../../../components/settings/ServicesFieldList.vue'

defineProps({
  section: { type: Object, required: true }
})
</script>

<template>
  <div class="services-panel">
    <ServicesFieldList :section="section" />
  </div>
</template>

<style scoped>
.services-panel { max-width: 520px; }
</style>
