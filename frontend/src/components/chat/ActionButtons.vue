<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  actions: {
    type: Array,
    default: () => []
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['action'])

const trackRef = ref(null)
const overflowing = ref(false)
const bounce = ref(false)
const presentation = ref(0)

function checkOverflow() {
  const el = trackRef.value
  overflowing.value = !!el && el.scrollWidth > el.clientWidth + 1
  if (overflowing.value) {
    bounce.value = false
    nextTick(() => { bounce.value = true })
  } else {
    bounce.value = false
  }
}

const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(checkOverflow)

watch(trackRef, (el, prevEl) => {
  if (prevEl) resizeObserver?.unobserve(prevEl)
  if (el) resizeObserver?.observe(el)
  checkOverflow()
})

watch(() => props.actions, () => {
  presentation.value += 1
  nextTick(checkOverflow)
}, { deep: true })

watch(() => props.disabled, (isDisabled) => {
  if (!isDisabled) presentation.value += 1
})

onBeforeUnmount(() => resizeObserver?.disconnect())
</script>

<template>
  <div class="action-buttons" v-if="actions.length">
    <div
      ref="trackRef"
      class="action-buttons-track"
      :class="{ 'is-overflowing': overflowing, 'is-bouncing': bounce, 'is-fitting': !overflowing }"
      @animationend="bounce = false"
    >
      <button
        v-for="action in actions"
        :key="presentation + ':' + action.name"
        class="action-btn"
        :disabled="disabled || action.disabled"
        @click="emit('action', action.name)"
      >
        {{ action.ui_button }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.action-buttons {
  padding: 0.75rem 1rem;
}

.action-buttons-track {
  display: flex;
  gap: 0.5rem;
  flex-wrap: nowrap;
  overflow-x: auto;
  scroll-snap-type: x proximity;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.action-buttons-track::-webkit-scrollbar {
  display: none;
}

.action-btn {
  flex: none;
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  font-size: 0.85rem;
  cursor: pointer;
  scroll-snap-align: start;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .action-buttons {
    padding: 0.5rem 0.75rem;
  }

  .action-buttons-track.is-fitting .action-btn {
    flex: 1 1 0;
    min-width: min-content;
  }
}

@media (hover: none) and (pointer: coarse) {
  .action-btn {
    min-height: 2.75rem;
  }
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-buttons-track.is-overflowing.is-bouncing {
  animation: action-buttons-bounce-hint 0.9s ease;
}

@keyframes action-buttons-bounce-hint {
  0% { transform: translateX(0); }
  35% { transform: translateX(-14px); }
  60% { transform: translateX(0); }
  75% { transform: translateX(-5px); }
  100% { transform: translateX(0); }
}

@media (prefers-reduced-motion: reduce) {
  .action-buttons-track.is-bouncing {
    animation: none;
  }
}
</style>

<style>
.action-buttons {
  background: #f5f5f7;
}

.action-btn {
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
}

@media (hover: hover) {
  .action-btn:hover:not(:disabled) {
    background: #4a6fa5;
    color: white;
  }
}
</style>
