<script setup>
import { ref } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'solid' }
})

const rootEl = ref(null)

defineExpose({ el: rootEl })
</script>

<template>
  <header ref="rootEl" class="app-header" :class="`app-header-${props.variant}`">
    <div class="app-header-left"><slot name="left" /></div>
    <div class="app-header-center"><slot name="center" /></div>
    <div class="app-header-right"><slot name="right" /></div>
  </header>
</template>

<style scoped>
.app-header {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 0.6rem;
  flex-shrink: 0;
}

.app-header-solid {
  padding: calc(0.75rem + var(--safe-area-top)) 1rem 0.75rem;
  border-bottom: 1px solid #ddd;
  background: white;
}

.app-header-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 20;
  padding: calc(0.75rem + var(--safe-area-top)) calc(0.75rem + var(--safe-area-right)) 0.75rem calc(0.75rem + var(--safe-area-left));
}

.app-header-left,
.app-header-right {
  display: flex;
  align-items: center;
  min-width: 0;
}

.app-header-left {
  gap: 0.6rem;
  justify-self: start;
}

.app-header-right {
  gap: 0.5rem;
  justify-self: end;
}

.app-header-center {
  display: flex;
  align-items: center;
  justify-self: center;
  min-width: 0;
}
</style>

<style>
.app-header-icon-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  padding: 0;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.app-header-icon-btn:hover {
  background: #4a6fa5;
  color: white;
}

.app-header-overlay .app-header-icon-btn,
.app-header-overlay .projects-btn {
  opacity: 0.35;
  transition: opacity 0.15s ease;
}

.app-header-overlay .app-header-icon-btn:hover,
.app-header-overlay .projects-btn:hover {
  opacity: 1;
}

@media (hover: none) and (pointer: coarse) {
  .app-header-overlay .app-header-icon-btn,
  .app-header-overlay .projects-btn {
    width: 2.75rem;
    height: 2.75rem;
    opacity: 1;
  }
}

.app-header-title {
  margin: 0;
  font-size: 1.1rem;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
