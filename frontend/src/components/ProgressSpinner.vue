<script setup>
defineProps({
  progress: { type: Number, default: null }
})
</script>

<template>
  <svg v-if="progress == null" viewBox="0 0 24 24" width="16" height="16" fill="none" class="progress-spinner-indeterminate">
    <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="42 14" />
  </svg>
  <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none" class="progress-spinner-ring">
    <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" opacity="0.25" />
    <circle
      cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
      stroke-dasharray="56.55" :stroke-dashoffset="56.55 * (1 - progress / 100)"
      transform="rotate(-90 12 12)"
    />
  </svg>
</template>

<style scoped>
.progress-spinner-indeterminate {
  animation: progress-spinner-spin 0.8s linear infinite;
}

@keyframes progress-spinner-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
