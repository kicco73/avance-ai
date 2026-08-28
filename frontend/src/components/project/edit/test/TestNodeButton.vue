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
// (still 'ready' but never picked up — reports percentage===0,
// indistinguishable from "genuinely just began") spins indeterminately
// instead — except while actually 'running': that arc is real and worth
// showing even at 0%, so it never falls back to the indeterminate spin.
const hasProgress = computed(() => props.status === 'running' || (props.progress != null && props.progress > 0))
// A 0% arc is an invisible arc (stroke-dasharray "0 100") — without a
// floor, the exact instant a job starts running the visible indeterminate
// ring would vanish into nothing behind the lightning, reading as "the
// ring disappeared" rather than "the same ring, now showing real
// progress and holding still." A small minimum keeps it a visible,
// continuous ring throughout.
const progressPercent = computed(() => {
  if (!hasProgress.value) return 0
  const percent = Math.min(100, Math.round(props.progress))
  return props.status === 'running' ? Math.max(percent, 8) : percent
})
const isBusy = computed(() => ['pending', 'ready', 'running', 'paused'].includes(props.status))
// One ring, always — idle/ok/warning/fail draw it fully closed (same
// <circle>, same radius, same stroke as every busy state), so there is
// never a second, separately-sized shape (a CSS border) standing in for
// it — nothing to visibly mismatch when a node's status flips.
const ringDasharray = computed(() => {
  if (!isBusy.value) return '100 100'
  return hasProgress.value ? `${progressPercent.value} 100` : '50 100'
})
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
    <svg viewBox="0 0 24 24" width="100%" height="100%">
      <circle
        class="test-node-btn-ring"
        :class="{ 'test-node-btn-spinner-indeterminate': isBusy && !hasProgress }"
        cx="12" cy="12" r="10.9" pathLength="100"
        fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
        :stroke-dasharray="ringDasharray"
      />
      <path
        v-if="status === 'idle'"
        d="M8 5v14l11-7z"
        transform="translate(12 12) scale(0.7) translate(-12 -12)"
        fill="currentColor"
      />
      <g v-else-if="status === 'paused'" transform="translate(12 12) scale(0.6) translate(-12 -12)">
        <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" />
        <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" />
      </g>
      <!-- 'running': a worker is inside this exact job's step right now —
           the lightning marks that specific instant, not "this kind of
           node is high-priority" (a priority node just gets picked up
           sooner; once picked up it runs exactly like any other). Drawn
           inside the same ring as the progress arc, not swapped in place
           of it, so the percentage stays visible while it's running too. -->
      <path
        v-else-if="status === 'running'"
        class="test-node-btn-lightning"
        d="M11 21v-8H7l6-11v8h4l-6 11z"
        transform="translate(12 12) scale(0.75) translate(-12 -12)"
        fill="currentColor"
      />
      <polyline
        v-else-if="status === 'ok'"
        class="test-node-btn-check"
        points="4 13 9 18 20 6"
        pathLength="100"
        transform="translate(12 12) scale(0.6) translate(-12 -12)"
        fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"
      />
      <path
        v-else-if="status === 'warning'"
        d="M12 3L1 21h22L12 3zm0 5.5l6.6 11.5H5.4L12 8.5zM11 11v4h2v-4h-2zm0 5v2h2v-2h-2z"
        transform="translate(12 12) scale(0.5) translate(-12 -12)"
        fill="currentColor"
      />
      <path
        v-else-if="status === 'fail'"
        d="M12 2a10 10 0 100 20 10 10 0 000-20zm3.5 13.1L15.1 16.5 12 13.4l-3.1 3.1-1.4-1.4L10.6 12 7.5 8.9l1.4-1.4L12 10.6l3.1-3.1 1.4 1.4L13.4 12z"
        transform="translate(12 12) scale(0.6) translate(-12 -12)"
        fill="currentColor"
      />
    </svg>
  </button>
</template>

<style scoped>
.test-node-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 1.2075rem;
  height: 1.2075rem;
  border-radius: 50%;
  border: none;
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

/* On hover the solid fill already reads as the button's shape — the ring
   is a stroke sitting slightly inset from the true edge (it has to, to
   avoid clipping), so drawn on top of the fill it reads as a second,
   not-quite-matching circle. Simplest fix: let the fill alone stand once
   hovered, no ring competing with it. */
.test-node-btn:hover:not(:disabled) .test-node-btn-ring {
  display: none;
}

.test-node-btn-disabled {
  color: #ccc;
  background: white;
}

.test-node-btn-ready {
  background: none;
  color: #4a6fa5;
}

.test-node-btn-running {
  background: none;
  color: #2e7d32;
}

.test-node-btn-lightning {
  animation: test-node-btn-glow 0.9s ease-in-out infinite alternate;
}

@keyframes test-node-btn-glow {
  from { opacity: 1; }
  to { opacity: 0.3; }
}

.test-node-btn-ring {
  transform-origin: center;
  transform: rotate(-90deg);
  transition: stroke-dasharray 0.2s linear, transform 0.2s linear;
}

.test-node-btn-ring.test-node-btn-spinner-indeterminate {
  animation: test-node-btn-spin 0.9s linear infinite;
}

@keyframes test-node-btn-spin {
  from { transform: rotate(-90deg); }
  to { transform: rotate(270deg); }
}

.test-node-btn-ok {
  color: #2e7d32;
}

.test-node-btn-check {
  stroke-dasharray: 100;
  stroke-dashoffset: 100;
  animation: test-node-btn-check-draw 0.4s ease-out forwards;
}

@keyframes test-node-btn-check-draw {
  to { stroke-dashoffset: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .test-node-btn-check {
    animation: none;
    stroke-dashoffset: 0;
  }
}

.test-node-btn-ok:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.test-node-btn-warning {
  color: #b26a00;
}

.test-node-btn-warning:hover:not(:disabled) {
  background: #b26a00;
  color: white;
}

.test-node-btn-fail {
  color: #c62828;
}

.test-node-btn-fail:hover:not(:disabled) {
  background: #c62828;
  color: white;
}
</style>
