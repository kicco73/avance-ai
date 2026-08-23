<script setup>
// Play/status control shared by every level of the "Test" tab's tree —
// root and the two branch nodes run every test in their scope at once,
// same gesture as a single leaf. Purely presentational — only emits.
const props = defineProps({
  status: {
    type: String,
    default: 'idle',
    validator: (value) => ['idle', 'running', 'ok', 'warning', 'fail'].includes(value)
  },
  disabled: { type: Boolean, default: false },
  disabledReason: { type: String, default: 'No test for this node' }
})

const emit = defineEmits(['activate'])

function onClick() {
  // running: never clickable (can't re-launch an in-flight test). Any other status,
  // including a past outcome, is a legitimate click — activate there means "re-run".
  if (props.disabled || props.status === 'running') return
  emit('activate')
}
</script>

<template>
  <button
    type="button"
    class="test-node-btn"
    :class="[`test-node-btn-${status}`, { 'test-node-btn-disabled': disabled }]"
    :disabled="disabled || status === 'running'"
    :title="disabled ? disabledReason : (status === 'running' ? 'Running…' : 'Run test')"
    @click="onClick"
  >
    <svg v-if="status === 'idle'" viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
    <svg v-else-if="status === 'running'" class="test-node-btn-spinner" viewBox="0 0 24 24" width="14" height="14">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="28 100" />
    </svg>
    <svg v-else-if="status === 'ok'" viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <path d="M9 16.2l-3.5-3.5-1.4 1.4L9 19 20 8l-1.4-1.4z" />
    </svg>
    <svg v-else-if="status === 'warning'" viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <path d="M12 3L1 21h22L12 3zm0 5.5l6.6 11.5H5.4L12 8.5zM11 11v4h2v-4h-2zm0 5v2h2v-2h-2z" />
    </svg>
    <svg v-else-if="status === 'fail'" viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
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
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
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

.test-node-btn-running {
  border-color: #4a6fa5;
  color: #4a6fa5;
}

.test-node-btn-spinner {
  animation: test-node-btn-spin 0.9s linear infinite;
}

@keyframes test-node-btn-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
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
