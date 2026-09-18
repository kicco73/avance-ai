<script setup>
import { toasts, dismissToast } from '../toastStore.js'
import { renderMarkdown } from '../markdown.js'
</script>

<template>
  <div class="toast-container">
    <TransitionGroup name="toast">
      <div v-for="toast in toasts" :key="toast.id" class="toast-card">
        <div class="toast-header">
          <span class="toast-title">{{ toast.title }}</span>
          <button class="toast-close" title="Dismiss" @click="dismissToast(toast.id)">×</button>
        </div>
        <div class="toast-body" v-html="renderMarkdown(toast.body)"></div>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-container {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  width: 320px;
  max-width: calc(100vw - 2rem);
  pointer-events: none;
}

.toast-card {
  pointer-events: auto;
  background: #ffffff;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-left: 4px solid #6366f1;
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18), 0 2px 8px rgba(15, 23, 42, 0.08);
  overflow: hidden;
  backdrop-filter: blur(6px);
}

.toast-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.65rem 0.5rem 0.65rem 0.8rem;
}

.toast-title {
  font-weight: 700;
  font-size: 0.88rem;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toast-close {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  line-height: 1;
  border: none;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.06);
  color: #6b7280;
  cursor: pointer;
  font-size: 1.05rem;
  transition: background 0.15s ease, color 0.15s ease;
}

.toast-close:hover {
  background: rgba(15, 23, 42, 0.12);
  color: #111827;
}

.toast-body {
  padding: 0 0.9rem 0.8rem 0.8rem;
  font-size: 0.85rem;
  line-height: 1.45;
  color: #4b5563;
}

.toast-body :deep(p) {
  margin: 0 0 0.4rem;
}

.toast-body :deep(p:last-child) {
  margin-bottom: 0;
}

.toast-enter-active, .toast-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(20px);
}

.toast-leave-to {
  opacity: 0;
}
</style>
