<script setup>
// EditProjectView.vue's own chat only (see TestChat.vue's restartAndPrefill/
// restartAndResend) — a reload-style icon that reads two distinct
// gestures apart, rather than needing a mode prop/branch anywhere else:
// - a single click: emit('click') — retry, resending the message as-is.
// - a quick double click: emit('double-click') — prefill for editing
//   instead, never auto-sent.
// The browser fires click, click, dblclick (in that order) for a real
// double-click, so a naive @click handler would fire the single-click
// action twice before dblclick ever lands. Standard disambiguation
// instead: each click starts a short timer before actually emitting
// 'click'; dblclick cancels that pending timer and emits 'double-click'
// in its place, so a real double-click only ever produces the one event.
const CLICK_DELAY_MS = 250

defineProps({
  // True once the state this bubble's own conversation was in has since
  // been renamed/removed from the project's own definition (see
  // EditProjectView.vue's validStateKeys/isStateGone) — restarting from
  // here would have nowhere valid to land, so the gesture is disabled
  // outright rather than left to fail against the backend.
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
