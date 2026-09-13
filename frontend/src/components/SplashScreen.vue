<script setup>
import { errorMessage } from '../errorStore.js'
import ErrorBanner from './ErrorBanner.vue'
import logoUrl from '../assets/avance-logo.png'

defineProps({
  variant: {
    type: String,
    default: 'connecting'
  },
  reason: {
    type: String,
    default: ''
  },
  embedded: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['retry'])
</script>

<template>
  <div class="splash" :class="{ 'splash-embedded': embedded }">
    <div class="splash-content">
      <img v-if="!embedded" :src="logoUrl" class="splash-logo" alt="Avance" />

      <template v-if="variant === 'connecting'">
        <div class="splash-pulse" aria-hidden="true"></div>
        <p class="splash-message">Connecting to the backend…</p>
      </template>

      <template v-else-if="variant === 'failed'">
        <ErrorBanner v-if="errorMessage" />
        <p v-else class="splash-error">Unable to reach the backend — check that it's running.</p>
        <button class="splash-retry" @click="emit('retry')">Retry</button>
      </template>

      <template v-else-if="variant === 'paused'">
        <p class="splash-message">Project under maintainance, please try again later.</p>
        <p v-if="reason" class="splash-paused-reason">{{ reason }}</p>
      </template>

      <template v-else>
        <p class="splash-message">No project is currently available.</p>
        <p class="splash-message">Please contact your admin to fix this.</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.splash {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-base-gradient);
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
  padding-bottom: var(--safe-area-bottom);
  box-sizing: border-box;
}

.splash-embedded {
  position: static;
  inset: auto;
  flex: 1;
  min-width: 0;
  min-height: 0;
  z-index: auto;
  background: white;
}

.splash-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  text-align: center;
  width: 345px;
  box-sizing: border-box;
  padding: 2.5rem 2rem;
  background: white;
  border-radius: 14px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.18);
  animation: splash-content-in 1s ease-out;
}

@keyframes splash-content-in {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.splash-embedded .splash-content {
  width: auto;
  padding: 1.5rem;
  background: none;
  border-radius: 0;
  box-shadow: none;
  animation: none;
}

.splash-logo {
  width: 150px;
  height: auto;
  margin-top: 0.8rem;
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

.splash-paused-reason {
  margin: 0;
  max-width: 320px;
  font-size: 0.82rem;
  color: #b06a00;
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
