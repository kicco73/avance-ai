<script setup>
import ProgressToast from './ProgressToast.vue'
import ChatNotificationToasts from './ChatNotificationToasts.vue'

defineProps({
  progress: { type: Object, default: null }
})
</script>

<template>
  <div class="chat-toast-stack">
    <ProgressToast :progress="progress" />
    <ChatNotificationToasts />
  </div>
</template>

<style scoped>
.chat-toast-stack {
  position: absolute;
  top: calc(var(--chat-header-height, 70px) + 0.75rem);
  left: 50%;
  z-index: 20;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.6rem;
  pointer-events: none;
}
</style>
