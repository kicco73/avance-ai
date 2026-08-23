<script setup>
// Per-message user reaction: a pick-one from the active project's own
// `reactions` vocabulary, attached to a bot message. No trigger of its
// own — MessageBubble.vue opens the picker via the exposed open()/close()
// below, off a long-press on the whole bubble, and passes the element to
// position it against. Modeled on MessageCommentButton.vue's own popover
// mechanics otherwise (teleport to body, click-away to close).
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  // {key, ui_label}[] — the active project's whole reaction vocabulary,
  // always the same list regardless of which state this message came from.
  reactions: { type: Array, default: () => [] },
  // null/'' = no reaction yet; a key matching one of `reactions` = the
  // currently chosen one, highlighted in the picker and shown as the
  // read-only display below.
  reaction: { type: String, default: null }
})

// 'save' carries the chosen key, or null to clear — the parent owns the
// actual API call, so this component has no idea one even exists.
const emit = defineEmits(['save'])

const open = ref(false)
const popoverRef = ref(null)
const style = ref({})

function openPopover(anchorEl) {
  if (!props.reactions.length) return
  const rect = anchorEl?.getBoundingClientRect?.()
  if (rect) style.value = { top: `${rect.bottom + 6}px`, left: `${rect.left}px` }
  open.value = true
}

function closePopover() {
  open.value = false
}

// Picking the already-active reaction again clears it — a plain toggle,
// since there's no separate "remove" control in a one-tap picker.
function pick(key) {
  emit('save', key === props.reaction ? null : key)
  closePopover()
}

function onDocumentMousedown(event) {
  if (popoverRef.value?.contains(event.target)) return
  closePopover()
}

watch(open, (isOpen) => {
  if (isOpen) document.addEventListener('mousedown', onDocumentMousedown)
  else document.removeEventListener('mousedown', onDocumentMousedown)
})

onBeforeUnmount(() => document.removeEventListener('mousedown', onDocumentMousedown))

defineExpose({ open: openPopover, close: closePopover })
</script>

<template>
  <Transition name="reaction-badge-pop">
    <span
      v-if="reaction"
      :key="reaction"
      class="reaction-display"
      :title="reactions.find((r) => r.key === reaction)?.ui_label"
    >{{ reactions.find((r) => r.key === reaction)?.ui_label ?? '·' }}</span>
  </Transition>

  <Teleport to="body">
    <Transition name="reaction-popover-pop">
      <div
        v-if="open"
        ref="popoverRef"
        class="reaction-popover"
        :style="style"
        @click.stop
        @keydown.esc="closePopover"
      >
        <button
          v-for="option in reactions"
          :key="option.key"
          type="button"
          class="reaction-popover-option"
          :class="{ 'reaction-popover-option-active': option.key === reaction }"
          :title="option.ui_label"
          @click="pick(option.key)"
        >{{ option.ui_label }}</button>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Sits inside MessageBubble.vue's own .reaction-badge-slot (absolutely
   positioned off the bubble's bottom-right corner) — same transparent,
   no-chrome treatment as its read-only counterpart on a user bubble. */
.reaction-display {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  line-height: 1;
  background: transparent;
}

/* Fade + bump on arrival — same animation as MessageBubble.vue's own
   .reaction-badge-pop, copied here since Vue's scoped styles never cross
   component files. Only on enter: a cleared reaction just vanishes. */
.reaction-badge-pop-enter-active {
  animation: reaction-badge-bump 0.4s ease-out;
}

@keyframes reaction-badge-bump {
  0% {
    opacity: 0;
    transform: scale(0.3);
  }
  60% {
    opacity: 1;
    transform: scale(1.25);
  }
  100% {
    opacity: 1;
    transform: scale(1);
  }
}

/* Teleported to <body>, position: fixed — see openPopover() above. */
.reaction-popover {
  position: fixed;
  z-index: 1000;
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  max-width: 220px;
  padding: 0.5rem;
  border-radius: 8px;
  background: white;
  border: 1px solid #ccc;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.15);
  transform-origin: bottom left;
}

.reaction-popover-option {
  width: 2rem;
  height: 2rem;
  border-radius: 6px;
  border: 1px solid transparent;
  background: none;
  cursor: pointer;
  font-size: 1.1rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.reaction-popover-option:hover {
  background: #f0f0f0;
}

.reaction-popover-option-active {
  border-color: #4a6fa5;
  background: #eaf1fb;
}

.reaction-popover-pop-enter-active,
.reaction-popover-pop-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.reaction-popover-pop-enter-from,
.reaction-popover-pop-leave-to {
  opacity: 0;
  transform: scale(0.85) translateY(-4px);
}
</style>
