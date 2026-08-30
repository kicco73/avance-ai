<script setup>
import { errorMessage } from '../errorStore.js'
import ErrorBanner from './ErrorBanner.vue'
import logoUrl from '../assets/avance-logo.png'

// 'connecting'/'failed': full-page overlay before the topbar renders.
// 'no-project'/'paused': topbar is showing, this fills the content area
// below it (see `embedded`) instead of covering the whole viewport.
defineProps({
  variant: {
    type: String,
    default: 'connecting' // 'connecting' | 'failed' | 'no-project' | 'paused'
  },
  // 'paused' only — human-readable reason shown under the headline message.
  reason: {
    type: String,
    default: ''
  },
  // Fills its parent flex container instead of covering the viewport —
  // set whenever the topbar must stay visible/interactive alongside it.
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
        <!-- A boot-ping timeout never reaches apiFetch's setApiError, so
             errorMessage can be empty even after every retry is exhausted —
             this fallback covers that case. -->
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
  /* height, not bottom: 0 (i.e. not inset: 0) — this screen showed a
     real, large (not safe-area-sized) gap at the bottom on this app's
     real deployment target (a standalone home-screen webapp on iOS)
     even with zero dynamic content, zero scrolling, zero JS-driven
     layout — position: fixed's own inset: 0 genuinely doesn't reach the
     true bottom of the screen there. var(--real-viewport-height) is
     window.innerHeight itself (see App.vue's own
     updateRealViewportHeight comment for why that's trustworthy where
     the CSS viewport units apparently aren't). */
  height: calc(var(--real-viewport-height, 100vh) + var(--safe-area-bottom));
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-base-gradient);
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
  padding-bottom: var(--safe-area-bottom);
  box-sizing: border-box;
}

/* Embedded: fills whatever flex slot it's given, alongside the
   still-visible topbar, instead of covering the whole viewport — stays
   white, blending into the chat area it sits in rather than the app's
   own base gradient. */
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

/* Embedded doesn't get the floating card treatment — it already sits
   inside its own flush white area (see .splash-embedded above). */
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
