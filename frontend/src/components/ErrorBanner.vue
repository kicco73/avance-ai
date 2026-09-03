<script setup>
// Shared error strip, one instance per screen. Every apiFetch failure
// lands in the same errorStore.js regardless of which screen triggered
// it, so this is the only place that ever needs to render it.
import { onUnmounted, ref, watch } from 'vue'
import { clearApiError, errorDetail, errorMessage, errorSeverity } from '../errorStore.js'

const showDetail = ref(false)

// A new error resets any expanded detail and restarts the auto-dismiss
// timer — each error gets its own full 10s. A 'warning' severity never
// auto-dismisses; it stays until the user closes it or the caller clears it.
const AUTO_DISMISS_MS = 10000
let dismissTimer = null
watch([errorMessage, errorSeverity], ([message, severity]) => {
  showDetail.value = false
  if (dismissTimer) clearTimeout(dismissTimer)
  dismissTimer = message && severity !== 'warning' ? setTimeout(clearApiError, AUTO_DISMISS_MS) : null
})

// Opening the detail means the user is reading it — cancel the timer so
// auto-dismiss doesn't close it out from under them.
watch(showDetail, (open) => {
  if (open && dismissTimer) {
    clearTimeout(dismissTimer)
    dismissTimer = null
  }
})
onUnmounted(() => {
  if (dismissTimer) clearTimeout(dismissTimer)
})
</script>

<template>
  <Transition name="error-banner-collapse">
    <div v-if="errorMessage" class="error-banner-wrap" :class="{ 'error-banner-wrap-warning': errorSeverity === 'warning' }">
      <div class="error-banner-row">
        <p class="error-banner-message">{{ errorMessage }}</p>
        <button
          v-if="errorDetail"
          type="button"
          class="error-banner-details-btn"
          @click="showDetail = !showDetail"
        >
          {{ showDetail ? 'Hide details' : 'Details' }}
        </button>
        <button type="button" class="error-banner-close-btn" title="Dismiss" @click="clearApiError">×</button>
      </div>
      <pre v-if="errorDetail && showDetail" class="error-banner-detail">{{ errorDetail }}</pre>
    </div>
  </Transition>
</template>

<style scoped>
/* Teleported to <body> (see App.vue's own comment on that) — no longer
   a flex child pushing .app-body down, so this is what actually keeps it
   pinned across the top of the viewport instead of just wherever it
   landed in <body>'s own child order. z-index above DialogHost.vue's own
   .app-dim (2000): stays legible over the dim scrim while a dialog is
   open, though a dialog's own top-layer promotion still always wins over
   any z-index if the two actually overlap on screen. */
.error-banner-wrap {
  position: fixed;
  top: var(--safe-area-top);
  left: 0;
  right: 0;
  z-index: 2050;
}

/* "Slide up": collapses the banner's height to 0 so content below it
   visibly scrolls up, rather than just fading in place. */
.error-banner-collapse-enter-active, .error-banner-collapse-leave-active {
  transition: max-height 0.25s ease, opacity 0.2s ease;
  overflow: hidden;
}
.error-banner-collapse-enter-from, .error-banner-collapse-leave-to {
  max-height: 0;
  opacity: 0;
}
.error-banner-collapse-enter-to, .error-banner-collapse-leave-from {
  max-height: 320px;
  opacity: 1;
}

.error-banner-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
}

.error-banner-message {
  margin: 0;
  color: #c62828;
  font-size: 0.9rem;
  flex: 1;
}

.error-banner-details-btn {
  flex: none;
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.8rem;
}

.error-banner-close-btn {
  flex: none;
  width: 1.6rem;
  height: 1.6rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
  font-size: 1.05rem;
}

.error-banner-close-btn:hover {
  background: rgba(198, 40, 40, 0.12);
}

.error-banner-detail {
  margin: 0;
  padding: 0.75rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
  color: #7a1f1f;
  font-size: 0.8rem;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

/* severity: 'warning' — amber instead of red, for "this isn't a
   failure, it's a state you should know about" styling. */
.error-banner-wrap-warning .error-banner-row {
  background: #fff4e0;
  border-bottom-color: #f0d9a8;
}

.error-banner-wrap-warning .error-banner-message {
  color: #b06a00;
}

.error-banner-wrap-warning .error-banner-details-btn {
  border-color: #b06a00;
  color: #b06a00;
}

.error-banner-wrap-warning .error-banner-close-btn {
  color: #b06a00;
}

.error-banner-wrap-warning .error-banner-close-btn:hover {
  background: rgba(176, 106, 0, 0.12);
}

.error-banner-wrap-warning .error-banner-detail {
  background: #fff4e0;
  border-bottom-color: #f0d9a8;
  color: #7a4f00;
}
</style>
