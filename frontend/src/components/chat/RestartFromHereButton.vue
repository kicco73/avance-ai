<script setup>
// Single click: emit('click') — retry, resending as-is.
// Double click: emit('double-click') — prefill for editing instead.
// The browser fires click, click, dblclick, so each click is delayed
// briefly and cancelled by a following dblclick, to avoid double-firing.
const CLICK_DELAY_MS = 250

defineProps({
  // True once the state this bubble's conversation was in has since been
  // renamed/removed from the project — restarting here would have
  // nowhere valid to land, so the gesture is disabled outright.
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['click', 'double-click'])

let pendingClickTimer = null

function handleClick() {
  clearPendingClick()
  pendingClickTimer = setTimeout(() => {
    pendingClickTimer = null
    emit('click')
  }, CLICK_DELAY_MS)
}

function clearPendingClick() {
  if (pendingClickTimer == null) return
  clearTimeout(pendingClickTimer)
  pendingClickTimer = null
}

function handleDoubleClick() {
  clearPendingClick()
  emit('double-click')
}
</script>

<template>
  <button
    type="button"
    class="restart-from-here-btn"
    :disabled="disabled"
    :title="disabled ? 'This bubble\'s own state no longer exists in the project' : 'Restart from here: click to retry, double click to edit and resend'"
    @click.stop="handleClick"
    @dblclick.stop="handleDoubleClick"
    @contextmenu.prevent
  >
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <path d="M12 5V2L7 7l5 5V8c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z" />
    </svg>
  </button>
</template>

<style scoped>
.restart-from-here-btn {
  flex: none;
  align-self: center;
  margin-right: 0.4rem;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 50%;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  touch-action: none;
  user-select: none;
}

.restart-from-here-btn:hover:not(:disabled) {
  background: #f0f0f0;
  color: #333;
}

.restart-from-here-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
