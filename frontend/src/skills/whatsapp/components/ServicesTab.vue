<script setup>
import ServicesFieldList from '../../../components/settings/ServicesFieldList.vue'
import ServicesSecretField from '../../../components/settings/ServicesSecretField.vue'

defineProps({
  section: { type: Object, required: true }
})

const MASKED_FIELDS = ['verify-token', 'app-secret', 'access-token']
const PLAIN_FIELDS = ['phone-number-id', 'phone-number', 'invite-prefix', 'graph-version', 'voice-replies']
</script>

<template>
  <div class="services-panel">
    <label class="services-checkbox-field">
      <input type="checkbox" :checked="section.enabled" disabled />
      Service enabled
    </label>
    <template v-if="section.enabled">
      <ServicesSecretField v-for="name in MASKED_FIELDS" :key="name" :name="name" :value="section[name]" />
      <ServicesFieldList :section="section" :skip="[...MASKED_FIELDS, 'enabled', 'mark-read']" />
      <label class="services-checkbox-field">
        <input type="checkbox" :checked="section['mark-read']" disabled />
        Mark read
      </label>
    </template>
  </div>
</template>

<style scoped>
.services-panel { max-width: 520px; }
.services-checkbox-field { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.9rem; font-size: 0.9rem; color: #333; }
</style>
