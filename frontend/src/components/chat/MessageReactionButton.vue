<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  reactions: { type: Array, default: () => [] },
  reaction: { type: String, default: null }
})

const emit = defineEmits(['save'])

const open = ref(false)
const popoverRef = ref(null)
const style = ref({})

async function openPopover(anchorEl) {
  if (!props.reactions.length) return
  const rect = anchorEl?.getBoundingClientRect?.()
  if (rect) style.value = { top: `${rect.bottom + 6}px`, left: `${rect.left}px` }
  open.value = true
  if (!rect) return
  await nextTick()
  const popoverRect = popoverRef.value?.getBoundingClientRect()
  if (!popoverRect) return
  const roomAbove = rect.top - 6 >= popoverRect.height
  const fitsBelow = popoverRect.bottom <= window.innerHeight
  const top = fitsBelow
    ? rect.bottom + 6
    : roomAbove
      ? rect.top - popoverRect.height - 6
      : Math.max(6, window.innerHeight - popoverRect.height - 6)
  const left = Math.min(rect.left, window.innerWidth - popoverRect.width - 6)
  style.value = { top: `${top}px`, left: `${Math.max(6, left)}px` }
}

function closePopover() {
  open.value = false
}

function pick(key) {
  emit('save', key === props.reaction ? null : key)
  closePopover()
}

function onDocumentPointerdown(event) {
  if (popoverRef.value?.contains(event.target)) return
  closePopover()
}

function onAncestorScroll() {
  closePopover()
}

watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener('pointerdown', onDocumentPointerdown)
    document.addEventListener('scroll', onAncestorScroll, true)
  } else {
    document.removeEventListener('pointerdown', onDocumentPointerdown)
    document.removeEventListener('scroll', onAncestorScroll, true)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerdown)
  document.removeEventListener('scroll', onAncestorScroll, true)
})

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
.reaction-display {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  line-height: 1;
  background: transparent;
}

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

.reaction-popover {
  position: fixed;
  z-index: 1000;
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  max-width: 220px;
  max-height: min(320px, calc(100vh - 12px));
  overflow-y: auto;
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
