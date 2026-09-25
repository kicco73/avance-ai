<template>
  <div class="chat-toast-card"><slot /></div>
</template>

<style scoped>
.chat-toast-card {
  --chat-toast-shadow-blur: 16px;
  box-sizing: border-box;
  width: min(22rem, calc(95vw - 2 * var(--chat-toast-shadow-blur)));
  padding: 0.85rem 1rem 0.95rem;
  border-radius: 1rem;
  background: rgba(22, 24, 30, 0.72);
  backdrop-filter: blur(14px) saturate(160%);
  -webkit-backdrop-filter: blur(14px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 6px var(--chat-toast-shadow-blur) rgba(0, 0, 0, 0.28), 0 2px 6px rgba(0, 0, 0, 0.18);
  color: #fff;
}
</style>

<style>
.chat-toast-enter-active,
.chat-toast-leave-active {
  transition: opacity 0.45s ease, transform 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}

.chat-toast-enter-from,
.chat-toast-leave-to {
  opacity: 0;
  transform: translateY(-0.75rem) scale(0.97);
}

@media (prefers-reduced-motion: reduce) {
  .chat-toast-enter-active,
  .chat-toast-leave-active {
    transition: opacity 0.2s ease;
  }
}
</style>
