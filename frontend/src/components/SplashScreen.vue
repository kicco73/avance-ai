<script setup>
defineProps({
  failed: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['retry'])
</script>

<template>
  <div class="splash">
    <div class="splash-content">
      <h1 class="splash-title">Avance</h1>

      <template v-if="!failed">
        <div class="splash-pulse" aria-hidden="true"></div>
        <p class="splash-message">Connecting to the backend…</p>
      </template>

      <template v-else>
        <p class="splash-error">
          Unable to reach the backend — check that it's running.
        </p>
        <button class="splash-retry" @click="emit('retry')">Retry</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* Calm, clinical waiting state — a slow breathing pulse, not a spinner or
   anything that reads as playful/notification-like (same restraint already
   applied to the message chime). */
.splash {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: white;
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
}

.splash-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  text-align: center;
  padding: 1.5rem;
}

.splash-title {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
  color: #4a6fa5;
  letter-spacing: 0.02em;
}

.splash-pulse {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #4a6fa5;
  animation: splash-pulse 1.8s ease-in-out infinite;
}

@keyframes splash-pulse {
  0%,
  100% {
    opacity: 0.6;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.03);
  }
}

.splash-message {
  margin: 0;
  font-size: 0.9rem;
  color: #777;
}

.splash-error {
  margin: 0;
  max-width: 320px;
  font-size: 0.9rem;
  color: #c62828;
}

.splash-retry {
  padding: 0.5rem 1.4rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.9rem;
}

.splash-retry:hover {
  background: #4a6fa5;
  color: white;
}
</style>
