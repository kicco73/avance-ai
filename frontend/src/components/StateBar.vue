<script setup>
defineProps({
  state: {
    type: Object,
    default: null
  }
})

function badgeClass(state) {
  if (!state) return ''
  if (state.key === 'crisis') return 'badge-crisis'
  if (state.key === 'maintenance') return 'badge-maintenance'
  if (state.final) return 'badge-final'
  return 'badge-active'
}
</script>

<template>
  <div class="state-bar" v-if="state?.key">
    <div class="state-info">
      <span class="badge" :class="badgeClass(state)">{{ state.ui_label }}</span>
      <p class="ui_description">{{ state.ui_description }}</p>
    </div>
  </div>
</template>

<style scoped>
.state-bar {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1 1 auto;
}

.state-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
}

.badge {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-weight: 600;
  font-size: 0.85rem;
  color: white;
  white-space: nowrap;
}

.badge-active {
  background: #4a6fa5;
}

.badge-final {
  background: #2e7d32;
}

.badge-maintenance {
  background: #2e7d32;
}

.badge-crisis {
  background: #c62828;
}

.ui_description {
  margin: 0;
  font-size: 0.85rem;
  color: #444;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
