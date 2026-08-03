<script setup>
// EditProjectView.vue's own chat only (see its restartAndPrefill/
// restartAndResend) — a reload-style icon that reads two distinct
// gestures apart, rather than needing a mode prop/branch anywhere else:
// - long press: emit('long-press') once, then stays silent even if the
//   press continues (fires exactly once per press).
// - a quick double click: the browser's own native dblclick, untouched.
// Each pointerdown starts its own timer and each pointerup/leave/cancel
// clears it, so a real double-click (two short presses) never
// accidentally fires the long-press action in between.
const LONG_PRESS_MS = 600

defineProps({
  // True once the state this bubble's own conversation was in has since
  // been renamed/removed from the project's own definition (see
  // EditProjectView.vue's validStateKeys/isStateGone) — restarting from
  // here would have nowhere valid to land, so the gesture is disabled
  // outright rather than left to fail against the backend.
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['long-press', 'double-click'])

let timer = null

function startPress(event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  clearTimer()
  timer = setTimeout(() => {
    timer = null
    emit('long-press')
  }, LONG_PRESS_MS)
}

function clearTimer() {
  if (timer == null) return
  clearTimeout(timer)
  timer = null
}
</script>

<template>
  <button
    type="button"
    class="restart-from-here-btn"
    :disabled="disabled"
    :title="disabled ? 'This bubble\'s own state no longer exists in the project' : 'Restart from here: long press to clear, double click to resend'"
    @pointerdown.stop="startPress"
    @pointerup.stop="clearTimer"
    @pointerleave.stop="clearTimer"
    @pointercancel.stop="clearTimer"
    @click.stop
    @dblclick.stop="emit('double-click')"
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
