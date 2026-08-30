<script setup>
// The text input plus its side buttons (mic / audio / spoken-text) — a
// self-contained, presentational piece. All chat business logic stays in
// the parent; this component only renders and emits.
import { nextTick, ref, watch } from 'vue'

defineProps({
  disabled: { type: Boolean, default: false },
  recording: { type: Boolean, default: false },
  micAvailable: { type: Boolean, default: false },
  talkAvailable: { type: Boolean, default: false },
  audioEnabled: { type: Boolean, default: false },
  spokenTextEnabled: { type: Boolean, default: false }
})

const draft = defineModel({ type: String, default: '' })

const emit = defineEmits(['submit', 'mic-start', 'mic-stop', 'toggle-audio', 'toggle-spoken-text'])

const inputRef = ref(null)

defineExpose({ focus: () => inputRef.value?.focus() })

// Grows the textarea with its content (up to CSS's own max-height, which
// takes over with its own scrollbar past that) instead of a fixed
// single-line input that scrolls its text sideways.
//
// Measuring is a two-step dance because a CSS transition is active on
// height (see .input-row textarea below): reading scrollHeight
// synchronously right after changing a *transitioning* height reflects
// the pre-transition box, not the real content need, so a naive
// set-'auto'-then-read-scrollHeight got stuck on whatever the tallest
// height-so-far was instead of ever shrinking back down. Fix: measure
// with the transition switched off (so the reset is instant, not
// animated), then restore the *visually current* height and force a
// reflow before re-enabling the transition, so the animation the user
// sees still runs from where the box actually was, not from the
// throwaway reset value.
function autosize() {
  const el = inputRef.value
  if (!el) return
  const prevHeight = el.offsetHeight
  const prevTransition = el.style.transition
  el.style.transition = 'none'
  el.style.height = 'auto'
  const needed = el.scrollHeight
  el.style.height = `${prevHeight}px`
  void el.offsetHeight
  el.style.transition = prevTransition
  el.style.height = `${needed}px`
}

watch(draft, () => nextTick(autosize))

// Enter sends (matching the old single-line input's implicit behavior);
// Shift+Enter inserts a newline. isComposing guards an IME's own Enter
// (confirming a candidate) from being read as "send".
function onKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  emit('submit')
}

// iOS Safari sometimes scrolls the *document* to bring a newly-focused
// input above the keyboard, even with html/body's overflow: hidden (see
// App.vue) — a real, finger-draggable scroll, not a rendering glitch.
// LiveChatWindow.vue's own fixed positioning already tracks the visual
// viewport for sizing, but that scroll needs cancelling outright; it
// can land a frame or more after focus, hence the retries.
function onFocus() {
  window.scrollTo(0, 0)
  requestAnimationFrame(() => window.scrollTo(0, 0))
  setTimeout(() => window.scrollTo(0, 0), 300)
}
</script>

<template>
  <form class="input-row" @submit.prevent="emit('submit')">
    <textarea
      ref="inputRef"
      v-model="draft"
      rows="1"
      placeholder="Type a message..."
      :disabled="disabled"
      enterkeyhint="send"
      autocapitalize="sentences"
      autocomplete="off"
      spellcheck="true"
      @keydown="onKeydown"
      @focus="onFocus"
    ></textarea>

    <button
      v-if="micAvailable"
      type="button"
      class="mic-btn"
      :class="{ 'mic-btn-recording': recording }"
      :disabled="!recording && disabled"
      :title="recording ? 'Release to send' : 'Hold to record a voice message'"
      @pointerdown.prevent="emit('mic-start', $event)"
      @pointerup="emit('mic-stop')"
      @pointerleave="emit('mic-stop')"
      @pointercancel="emit('mic-stop')"
      @contextmenu.prevent
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z" />
        <path d="M17 11a1 1 0 1 0-2 0 3 3 0 0 1-6 0 1 1 0 1 0-2 0 5 5 0 0 0 4 4.9V18H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.1A5 5 0 0 0 17 11z" />
      </svg>
    </button>

    <button
      v-if="talkAvailable"
      type="button"
      class="audio-btn"
      :class="{ 'audio-btn-on': audioEnabled }"
      :title="audioEnabled ? 'Audio: On' : 'Audio: Off'"
      @click="emit('toggle-audio')"
    >
      <svg v-if="audioEnabled" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M3 9v6h4l5 5V4L7 9H3z" />
        <path d="M14.5 3.23v2.06c2.89 1.2 5 4.03 5 7.71s-2.11 6.51-5 7.71v2.06c4.01-1.28 7-5.09 7-9.77s-2.99-8.49-7-9.77zM16.5 12c0-1.77-.77-3.29-2-4.34v8.68c1.23-1.05 2-2.57 2-4.34z" />
      </svg>
      <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M3 9v6h4l5 5V4L7 9H3z" />
        <path d="M19.73 21 21 19.73l-9-9L4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.8L19.73 21zM19 12c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89 1.2 5 4.03 5 7.71zm-4.5-2.51v.13l2.44 2.44c.04-.28.06-.56.06-.85 0-1.77-.77-3.29-2-4.34-.16-.14-.33-.27-.5-.38z" />
      </svg>
    </button>

    <button
      v-if="talkAvailable"
      type="button"
      class="spoken-text-btn"
      :class="{ 'spoken-text-btn-on': spokenTextEnabled }"
      :title="spokenTextEnabled ? 'Showing spoken text' : 'Show spoken text'"
      @click="emit('toggle-spoken-text')"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M19 4H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7.5H9.5v-.5h-2v2h2v-.5H11V15c0 .55-.45 1-1 1H6c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5zm7 0h-1.5v-.5h-2v2h2v-.5H18V15c0 .55-.45 1-1 1h-4c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5z" />
      </svg>
    </button>
  </form>
</template>

<style scoped>
.input-row {
  display: flex;
  /* Not the default stretch: the side buttons would otherwise grow to
     match the textarea's own height as it grows across multiple lines.
     flex-end keeps them at their own fixed size, anchored to the
     textarea's bottom edge. */
  align-items: flex-end;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  /* Home indicator (iOS) / gesture nav bar (Android) sits right under
     this row otherwise — env() resolves to 0 on a device with neither,
     and unconditionally (not just under the max-width below): a
     landscape phone can easily be wider than that breakpoint while
     still having a home indicator to clear. This is the chat's actual
     footer edge, so it's the one place that reserves the bottom safe
     area — see LiveChatWindow.vue's own top/left/right padding for the
     rest. */
  padding-bottom: max(0.75rem, env(safe-area-inset-bottom));
  border-top: 1px solid #ddd;
}

@media (max-width: 640px) {
  .input-row {
    padding: 0.5rem 0.75rem;
    padding-bottom: max(0.5rem, env(safe-area-inset-bottom));
  }
}

.input-row textarea {
  flex: 1;
  /* Without this a flex item won't shrink below its content's intrinsic
     width — with mic/audio/spoken-text all showing, that pushed the
     later buttons out of the row entirely on narrow screens instead of
     yielding space to them. */
  min-width: 0;
  /* autosize() below sets height directly from scrollHeight, which (per
     spec) already includes padding — with the default content-box, that
     same padding then gets added a second time on top, inflating the
     box by 2x padding on every resize. border-box makes height and
     scrollHeight refer to the same box. */
  box-sizing: border-box;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  /* 16px: below this, iOS Safari zooms the page in on focus and doesn't
     zoom back out on blur. */
  font-size: 1rem;
  font-family: inherit;
  line-height: 1.4;
  resize: none;
  max-height: 6.5em;
  overflow-y: auto;
  /* Animates the grow/shrink set by autosize() above (always an explicit
     px value, never 'auto', so there are two real numbers to tween). */
  transition: height 0.15s ease-out;
}

.mic-btn,
.audio-btn,
.spoken-text-btn {
  flex: none;
  width: 2.5rem;
  /* Fixed, not stretched (see .input-row's align-items: flex-end) —
     matches the textarea's own one-line height so the row reads as a
     single tidy baseline before it ever grows. */
  height: 2.5rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* No mouse precision to rely on — grows each side button to the ~44px
   minimum recommended touch target (iOS HIG / Material). */
@media (hover: none) and (pointer: coarse) {
  .mic-btn,
  .audio-btn,
  .spoken-text-btn {
    width: 2.75rem;
    height: 2.75rem;
  }
}

.mic-btn {
  touch-action: none;
  user-select: none;
}

.mic-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.mic-btn-recording {
  border-color: #c62828;
  background: #c62828;
  color: white;
  animation: mic-pulse 1.2s ease-in-out infinite;
}

.mic-btn-recording:hover {
  background: #a02020;
}

.mic-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.5); }
  50% { box-shadow: 0 0 0 6px rgba(198, 40, 40, 0); }
}

.audio-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.audio-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.audio-btn-on:hover:not(:disabled) {
  background: #256428;
}

.audio-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spoken-text-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.spoken-text-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.spoken-text-btn-on:hover:not(:disabled) {
  background: #256428;
}
</style>
