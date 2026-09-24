<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'

const SHOWN_MS = 5000

const props = defineProps({
  progress: { type: Object, default: null }
})

const visible = ref(false)
const title = ref('')
const width = ref(0)
const reached = ref(0)
let hideTimer = null
let frame = null

function clamp(value) {
  return Math.max(0, Math.min(100, Number(value) || 0))
}

function growTo(target) {
  cancelAnimationFrame(frame)
  width.value = reached.value
  frame = requestAnimationFrame(() => {
    frame = requestAnimationFrame(() => { width.value = target })
  })
  reached.value = target
}

watch(() => props.progress, (next) => {
  if (!next) return
  title.value = next.title
  visible.value = true
  growTo(clamp(next.percentage))
  clearTimeout(hideTimer)
  hideTimer = setTimeout(() => { visible.value = false }, SHOWN_MS)
})

onBeforeUnmount(() => {
  clearTimeout(hideTimer)
  cancelAnimationFrame(frame)
})
</script>

<template>
  <Transition name="progress-toast">
    <div
      v-if="visible"
      class="progress-toast"
      role="progressbar"
      :aria-valuenow="reached"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="title"
    >
      <div class="progress-toast-head">
        <span class="progress-toast-title">{{ title }}</span>
        <span class="progress-toast-value">{{ Math.round(reached) }}%</span>
      </div>
      <div class="progress-toast-track">
        <div class="progress-toast-fill" :style="{ width: width + '%' }"></div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.progress-toast {
  position: absolute;
  top: calc(var(--chat-header-height, 70px) + 0.75rem);
  left: 50%;
  z-index: 20;
  --progress-toast-shadow-blur: 16px;
  box-sizing: border-box;
  width: min(22rem, calc(95vw - 2 *var(--progress-toast-shadow-blur)));
  transform: translateX(-50%);
  padding: 0.85rem 1rem 0.95rem;
  border-radius: 1rem;
  background: rgba(22, 24, 30, 0.72);
  backdrop-filter: blur(14px) saturate(160%);
  -webkit-backdrop-filter: blur(14px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 6px var(--progress-toast-shadow-blur) rgba(0, 0, 0, 0.28), 0 2px 6px rgba(0, 0, 0, 0.18);
  color: #fff;
  pointer-events: none;
}

.progress-toast-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.6rem;
}

.progress-toast-title {
  font-size: 0.9rem;
  font-weight: 600;
  letter-spacing: 0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-toast-value {
  flex-shrink: 0;
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
  opacity: 0.7;
}

.progress-toast-track {
  position: relative;
  height: 0.5rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  overflow: hidden;
}

.progress-toast-fill {
  position: relative;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #5ee7df 0%, #7f7bff 55%, #c56cf0 100%);
  box-shadow: 0 0 12px rgba(127, 123, 255, 0.65);
  transition: width 1.1s cubic-bezier(0.22, 1, 0.36, 1);
  overflow: hidden;
}

.progress-toast-fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.45) 50%, transparent 100%);
  transform: translateX(-100%);
  animation: progress-toast-shine 1.6s ease-in-out infinite;
}

@keyframes progress-toast-shine {
  to {
    transform: translateX(100%);
  }
}

.progress-toast-enter-active,
.progress-toast-leave-active {
  transition: opacity 0.45s ease, transform 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}

.progress-toast-enter-from,
.progress-toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-0.75rem) scale(0.97);
}

@media (prefers-reduced-motion: reduce) {
  .progress-toast-fill {
    transition: none;
  }

  .progress-toast-fill::after {
    animation: none;
  }

  .progress-toast-enter-active,
  .progress-toast-leave-active {
    transition: opacity 0.2s ease;
  }
}
</style>
