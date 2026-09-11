<script setup>
import ServicesProviderSection from '../../../components/skillkit/ServicesProviderSection.vue'

defineProps({
  section: { type: Object, required: true }
})
</script>

<template>
  <ServicesProviderSection :section="section" />
</template>
