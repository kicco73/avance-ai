<script setup>
import ProgressSpinner from '../ProgressSpinner.vue'
</script>

<template>
  <div class="chat-waiting-panel">
    <ProgressSpinner />
  </div>
</template>

<style scoped>
.chat-waiting-panel {
  position: absolute;
  inset: 0;
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #4a6fa5;
  background: rgba(255, 255, 255, 0.62);
  pointer-events: all;
}

.chat-waiting-panel svg {
  width: 32px;
  height: 32px;
}
</style>
