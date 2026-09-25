<script setup>
import { computed, ref } from 'vue'
import { appStoreFileContentUrl } from '../../api.js'
import avanceLogoUrl from '../../../../assets/avance-logo.png'

const props = defineProps({
  app: { type: Object, required: true }
})

const iconFailed = ref(false)

const iconUrl = computed(() => (
  props.app.icon_file && !iconFailed.value ? appStoreFileContentUrl(props.app.id, props.app.icon_file) : avanceLogoUrl
))

const title = computed(() => props.app.ui_label || props.app.id || '')
const skills = computed(() => props.app.skills ?? [])
</script>

<template>
  <div class="app-identity-header">
    <img :src="iconUrl" class="app-identity-icon" alt="" @error="iconFailed = true" />

    <div class="app-identity">
      <h2 class="app-identity-title">{{ title }}</h2>
      <div class="app-identity-badges">
        <span v-for="skill in skills" :key="skill.key" class="app-identity-badge">{{ skill.ui_label }}</span>
        <span v-if="app.reactions_enabled" class="app-identity-badge">REACTIONS</span>
      </div>
    </div>

    <slot />
  </div>
</template>

<style scoped>
.app-identity-header {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  gap: 0.9rem;
}

.app-identity-icon {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: 13px;
  object-fit: cover;
}

.app-identity {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.app-identity-title {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.app-identity-badges {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 1.2rem;
}

.app-identity-badge {
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  background: #eef3fa;
  color: #4a6fa5;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}
</style>
