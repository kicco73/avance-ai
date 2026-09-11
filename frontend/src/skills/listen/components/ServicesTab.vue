<script setup>
import ServicesProviderSection from '../../../components/settings/ServicesProviderSection.vue'

defineProps({
  section: { type: Object, required: true }
})
</script>

<template>
  <ServicesProviderSection :section="section" />
</template>
