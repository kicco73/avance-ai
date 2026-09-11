<script setup>
// Play/pause-style status button — originally ManageProjectsView.vue's
// own per-project run/pause row button, extracted so a caller elsewhere
// (see ServicesView.vue's own AI-provider selection button) can reuse the
// identical look instead of a second copy. Purely presentational: the
// caller decides what `status`/`disabled` mean and what a click does.
const props = defineProps({
  // 'running' -> pause icon if disabled=false (pulsing green, click
  // actually pauses it), a plain pulsing dot if disabled=true (pulsing
  // green, "this is the active one" but there's nothing to click — see
  // ServicesView.vue's own AI-provider row, where the active provider
  // can't be paused from here at all).
  // 'paused' -> solid square (external/automatic, amber).
  // 'manually_paused' -> play icon (gray-blue, "click to activate").
  status: { type: String, required: true },
  disabled: { type: Boolean, default: false },
  title: { type: String, default: '' },
  // Smaller footprint for a context tighter than ManageProjectsView's own
  // table row (e.g. one row per Manage services provider card).
  compact: { type: Boolean, default: false }
})

const emit = defineEmits(['click'])
</script>

<template>
  <button
    type="button"
    class="status-toggle-btn"
    :class="[`status-toggle-btn-${status}`, { 'status-toggle-btn-compact': compact }]"
    :disabled="disabled"
    :title="title"
    @click="emit('click')"
  >
    <span v-if="status === 'running' && disabled" class="status-toggle-dot"></span>
    <svg v-else-if="status === 'running'" viewBox="0 0 24 24" :width="compact ? 14 : 20" :height="compact ? 14 : 20" fill="currentColor">
      <path d="M8 5h3v14H8zM13 5h3v14h-3z" />
    </svg>
    <svg v-else-if="status === 'paused'" viewBox="0 0 24 24" :width="compact ? 14 : 20" :height="compact ? 14 : 20" fill="currentColor">
      <path d="M6 6h12v12H6z" />
    </svg>
    <svg v-else viewBox="0 0 24 24" :width="compact ? 14 : 20" :height="compact ? 14 : 20" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
  </button>
</template>

<style scoped>
.status-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 2.88rem;
  height: 2.88rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
}

.status-toggle-btn:not(:disabled):hover {
  background: #f0f4fa;
}

.status-toggle-btn:disabled {
  cursor: not-allowed;
}

.status-toggle-btn-running {
  color: #2e7d32;
  animation: status-toggle-pulse 2.2s ease-in-out infinite;
}

@keyframes status-toggle-pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

.status-toggle-btn-paused {
  color: #b06a00;
}

.status-toggle-btn-manually_paused {
  color: #607d8b;
}

.status-toggle-btn-compact {
  width: 1.8rem;
  height: 1.8rem;
}

.status-toggle-dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  background: currentColor;
}
</style>
