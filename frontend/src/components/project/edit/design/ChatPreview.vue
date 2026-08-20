<script setup>
// A real chat instance for previewing a project's own index.css "skin"
// while it's being edited — the same .chat-header/.chat-body/.chat-footer
// markup and the same real MessageBubble/ActionButtons/ChatInput
// components ChatWindow.vue itself uses (not a hand-rolled mock render),
// just fed a static fake conversation via props instead of chatStore.js's
// own live singleton (which has no notion of "unsaved, being-typed CSS"
// or "state picked from a pulldown" at all).
//
// The live CSS is injected as a real <style> element appended to
// document.head — the exact same technique ChatWindow.vue's own loadSkin
// uses (see its own docstring) — updated in place on every keystroke
// rather than replaced, so this is a plain reactive DOM update, never a
// full reload: state changes just toggle a class, and any transition the
// CSS itself defines (e.g. `transition: background-color .4s ease`)
// animates normally, exactly as it would in the real chat. Cleaned up on
// unmount so it never outlives this component.
import { onBeforeUnmount, ref, watch } from 'vue'
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

let styleEl = null

function applyCss(css) {
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.setAttribute('data-chat-preview-skin', '')
    document.head.appendChild(styleEl)
  }
  styleEl.textContent = css
}

watch(() => props.css, applyCss, { immediate: true })
onBeforeUnmount(() => { styleEl?.remove(); styleEl = null })
</script>

<template>
  <div class="chat-window-shell" :class="stateKey ? `state-${stateKey}` : null" :data-state="stateKey || null">
    <div class="chat-header"></div>
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
