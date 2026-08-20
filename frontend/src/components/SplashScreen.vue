<script setup>
import { errorMessage } from '../errorStore.js'
import ErrorBanner from './ErrorBanner.vue'

// 'connecting'/'failed': App.vue's own boot-ping sequence, a full-page
// overlay shown *before* the topbar itself ever renders (see App.vue's
// bootStatus). 'no-project': the app is up and the topbar is showing —
// this fills the content area below it instead (see the `embedded` prop),
// for whenever there's no active project to display (none ever
// uploaded, or the last/only one was just deleted — "default" is no
// longer protected from that, see ProjectsMenu.vue). 'paused' (Prompt 7):
// the active project itself exists but is currently unavailable (see
// chatStore.js's own projectPaused/projectPausedReason, set from GET
// /api/chat/session's own {paused, paused_reason} response) — shown in
// place of the chat window, same embedded slot as 'no-project'.
defineProps({
  variant: {
    type: String,
    default: 'connecting' // 'connecting' | 'failed' | 'no-project' | 'paused'
  },
  // 'paused' only — ProjectService.recompute_availability's own
  // human-readable reason, shown under the headline message when set.
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
      <h1 v-if="!embedded" class="splash-title">Avance</h1>

      <template v-if="variant === 'connecting'">
        <div class="splash-pulse" aria-hidden="true"></div>
        <p class="splash-message">Connecting to the backend…</p>
      </template>

      <template v-else-if="variant === 'failed'">
        <!-- A boot-ping timeout never reaches apiFetch's own setApiError
             (see App.vue's pingBackend — an AbortError is deliberately
             rethrown silently), so errorMessage can be empty even after
             every retry is exhausted — this fallback covers that case;
             ErrorBanner covers every other one uniformly. -->
        <ErrorBanner v-if="errorMessage" />
        <p v-else class="splash-error">Unable to reach the backend — check that it's running.</p>
        <button class="splash-retry" @click="emit('retry')">Retry</button>
      </template>

      <template v-else-if="variant === 'paused'">
        <p class="splash-message">Progetto in manutenzione, riprova più tardi.</p>
        <p v-if="reason" class="splash-paused-reason">{{ reason }}</p>
      </template>

      <template v-else>
        <p class="splash-message">Select a project from the menu to get started.</p>
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

/* Embedded (see the `embedded` prop): fills whatever flex slot it's
   given (App.vue's own .app-body, alongside the still-visible topbar)
   instead of covering the whole viewport. */
.splash-embedded {
  position: static;
  inset: auto;
  flex: 1;
  min-width: 0;
  min-height: 0;
  z-index: auto;
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
