<script setup>
// The client-side surface for an on-enter script's own `notify(title,
// body)` local (see onEnterActions.js) — mounted once, at App.vue's own
// root, so it renders above every view (main chat, EditProjectView's
// embedded Test chat, LabelProjectView) regardless of which one actually
// fired the action.
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
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
  overflow: hidden;
}

.toast-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  background: #f5f5f7;
  border-bottom: 1px solid #eee;
}

.toast-title {
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toast-close {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 1rem;
}

.toast-close:hover {
  background: #e5e5e5;
}

.toast-body {
  padding: 0.5rem 0.6rem;
  font-size: 0.82rem;
  line-height: 1.4;
  color: #444;
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
