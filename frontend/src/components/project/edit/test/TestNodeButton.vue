<script setup>
// Play/status control shared by every level of the "Test" tab's tree —
// root and the two branch nodes run every test in their scope at once,
// same gesture as a single leaf. Purely presentational — only emits.
import { computed } from 'vue'

const props = defineProps({
  status: {
    type: String,
    default: 'idle',
    validator: (value) => ['idle', 'pending', 'ready', 'running', 'paused', 'ok', 'warning', 'fail'].includes(value)
  },
  progress: { type: Number, default: null },
  disabled: { type: Boolean, default: false },
  disabledReason: { type: String, default: 'No test for this node' }
})

const emit = defineEmits(['activate'])

// Once any step has reported a real percentage, keep showing it — even
// while queued between steps ('ready'), not just at the instant a worker
// is actually inside a step ('running'). A node with no progress yet
// (still 'pending'/never started, or queued but never picked up — both
// report percentage===0, indistinguishable from "genuinely just began")
// spins indeterminately instead.
const hasProgress = computed(() => props.progress != null && props.progress > 0)
const progressPercent = computed(() => (hasProgress.value ? Math.min(100, Math.round(props.progress)) : 0))
const isBusy = computed(() => ['pending', 'ready', 'running', 'paused'].includes(props.status))
// 'running' — this queue's own view of the job (see JobQueue's
// ready/running/exited broadcasts, ThrottledJobQueue's added 'paused'),
// not anything the job tracks itself — means a worker is actively inside
// its step right now, and is the only busy state that gets the
// active/green treatment. Every other busy state — 'ready' (queued, no
// worker has picked it up yet) and 'paused' (throttled) — reads as the
// same "waiting" blue. With N concurrent workers, at most N buttons are
// ever green at once; every other in-flight one is blue.
const buttonState = computed(() => (props.status === 'running' ? 'running' : (isBusy.value ? 'ready' : props.status)))

function onClick() {
  // pending/ready/running/paused: never clickable (can't re-launch an
  // in-flight test). Any other status, including a past outcome, is a
  // legitimate click — activate there means "re-run".
  if (props.disabled || isBusy.value) return
  emit('activate')
}
</script>

<template>
  <button
    type="button"
    class="test-node-btn"
    :class="[`test-node-btn-${buttonState}`, { 'test-node-btn-disabled': disabled }]"
    :disabled="disabled || isBusy"
    :title="disabled ? disabledReason : (status === 'pending' || status === 'ready' ? 'Queued…' : status === 'paused' ? 'Paused…' : status === 'running' ? 'Running…' : 'Run test')"
    @click="onClick"
  >
    <svg v-if="status === 'idle'" viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
    <svg v-else-if="status === 'paused'" viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <rect x="6" y="4" width="4" height="16" rx="1" />
      <rect x="14" y="4" width="4" height="16" rx="1" />
    </svg>
    <!-- 'running': a worker is inside this exact job's step right now —
         the lightning marks that specific instant, not "this kind of
         node is high-priority" (a priority node just gets picked up
         sooner; once picked up it runs exactly like any other). -->
    <svg v-else-if="status === 'running'" viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
      <path d="M11 21v-8H7l6-11v8h4l-6 11z" />
    </svg>
    <svg
      v-else-if="isBusy"
      class="test-node-btn-spinner"
      :class="{ 'test-node-btn-spinner-indeterminate': !hasProgress }"
      viewBox="0 0 24 24" width="17" height="17"
    >
      <circle
        cx="12" cy="12" r="10" pathLength="100"
        fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
        :stroke-dasharray="hasProgress ? `${progressPercent} 100` : '50 100'"
      />
    </svg>
    <svg v-else-if="status === 'ok'" viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="4 13 9 18 20 6" />
    </svg>
    <svg v-else-if="status === 'warning'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
      <path d="M12 3L1 21h22L12 3zm0 5.5l6.6 11.5H5.4L12 8.5zM11 11v4h2v-4h-2zm0 5v2h2v-2h-2z" />
    </svg>
    <svg v-else-if="status === 'fail'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
      <path d="M12 2a10 10 0 100 20 10 10 0 000-20zm3.5 13.1L15.1 16.5 12 13.4l-3.1 3.1-1.4-1.4L10.6 12 7.5 8.9l1.4-1.4L12 10.6l3.1-3.1 1.4 1.4L13.4 12z" />
    </svg>
  </button>
</template>

<style scoped>
.test-node-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 1.15rem;
  height: 1.15rem;
  border-radius: 50%;
  border: 1.5px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
}

.test-node-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.test-node-btn:disabled {
  cursor: not-allowed;
}

.test-node-btn-disabled {
  border-color: #ccc;
  color: #ccc;
  background: white;
}

.test-node-btn-ready {
  border-color: transparent;
  color: #4a6fa5;
}

.test-node-btn-running {
  border-color: transparent;
  color: #2e7d32;
}

.test-node-btn-spinner {
  transform-origin: center;
  transform: rotate(-90deg);
  transition: transform 0.2s linear;
}

.test-node-btn-spinner circle {
  transition: stroke-dasharray 0.2s linear;
}

.test-node-btn-spinner-indeterminate {
  animation: test-node-btn-spin 0.9s linear infinite;
}

@keyframes test-node-btn-spin {
  from { transform: rotate(-90deg); }
  to { transform: rotate(270deg); }
}

.test-node-btn-ok {
  border-color: #2e7d32;
  color: #2e7d32;
}

.test-node-btn-ok:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.test-node-btn-warning {
  border-color: #b26a00;
  color: #b26a00;
}

.test-node-btn-warning:hover:not(:disabled) {
  background: #b26a00;
  color: white;
}

.test-node-btn-fail {
  border-color: #c62828;
  color: #c62828;
}

.test-node-btn-fail:hover:not(:disabled) {
  background: #c62828;
  color: white;
}
</style>
