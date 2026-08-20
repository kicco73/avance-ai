<script setup>
// Renders a real chat instance (not a mock) fed a static fake conversation, so a
// project's index.css "skin" can be previewed live. The CSS is injected as a <style>
// element in document.head, updated in place per keystroke, and removed on unmount.
//
// This component stays mounted (v-show, not v-if) through several ancestor
// layers — ProjectDesignPanel's own file switch, IndexCssEditorPanel's own
// Preview/Code segment toggle — so "unmount" alone isn't enough to keep its
// injected <style> from lingering (and diverging from whatever real
// ChatWindow's own skin <style> shows) while this preview isn't the thing
// on screen. An IntersectionObserver on the root element tracks actual
// visibility instead: a display:none ancestor (from any v-show layer above)
// collapses this element's geometry, which the observer reports as
// non-intersecting — so the injected CSS only ever exists while genuinely visible.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MessageBubble from '../../../chat/MessageBubble.vue'
import ActionButtons from '../../../chat/ActionButtons.vue'
import ChatInput from '../../../chat/ChatInput.vue'

const props = defineProps({
  css: { type: String, default: '' },
  stateKey: { type: String, default: '' }
})

const MOCK_MESSAGES = [
  { messageId: 'preview-1', role: 'assistant', content: 'Hi! How can I help you today?', timestamp: new Date().toISOString() },
  { messageId: 'preview-2', role: 'user', content: 'I have a question about my order.', timestamp: new Date().toISOString() },
  { messageId: 'preview-3', role: 'assistant', content: 'Sure — what would you like to know?', timestamp: new Date().toISOString() }
]

const draft = ref('')
const rootEl = ref(null)
const visible = ref(false)

let styleEl = null
let observer = null

function syncStyle() {
  if (!visible.value) {
    styleEl?.remove()
    styleEl = null
    return
  }
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.setAttribute('data-chat-preview-skin', '')
    document.head.appendChild(styleEl)
  }
  styleEl.textContent = props.css
}

watch([() => props.css, visible], syncStyle, { immediate: true })

onMounted(() => {
  // jsdom (unit tests) has no IntersectionObserver — fall back to "always
  // visible", the same unconditional-inject behavior this replaces, rather
  // than crashing or silently never showing the preview under test.
  if (typeof IntersectionObserver === 'undefined') {
    visible.value = true
    return
  }
  observer = new IntersectionObserver(([entry]) => { visible.value = entry.isIntersecting }, { threshold: 0 })
  if (rootEl.value) observer.observe(rootEl.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  styleEl?.remove()
  styleEl = null
})
</script>

<template>
  <div ref="rootEl" class="chat-window-shell" :class="stateKey ? `state-${stateKey}` : null" :data-state="stateKey || null">
    <div class="chat-header">
      <div class="chat-header-icon"></div>
    </div>
    <div class="messages chat-body">
      <MessageBubble v-for="msg in MOCK_MESSAGES" :key="msg.messageId" :message="msg" show-timestamp />
    </div>
    <div class="chat-footer">
      <ActionButtons :actions="[]" disabled :auto-tracking-enabled="false" />
      <ChatInput v-model="draft" disabled :recording="false" :mic-available="false" :talk-available="false" :audio-enabled="false" :spoken-text-enabled="false" />
    </div>
  </div>
</template>

<style scoped>
.chat-window-shell {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.chat-footer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}
</style>
