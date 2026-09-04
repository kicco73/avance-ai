<script setup>
import { ref, watch } from 'vue'
import MessageBubble from '../chat/MessageBubble.vue'
import ActionButtons from '../chat/ActionButtons.vue'
import ChatInput from '../chat/ChatInput.vue'
import ProgressSpinner from '../ProgressSpinner.vue'
import { getAppPreviewTranscript } from '../../api.js'

const props = defineProps({
  loading: { type: Boolean, default: false },
  appId: { type: String, default: null }
})

const DEFAULT_MESSAGES = [
  { messageId: 'app-store-preview-1', role: 'assistant', content: 'Hi! How can I help you today?', timestamp: new Date().toISOString() },
  { messageId: 'app-store-preview-2', role: 'user', content: 'I have a question about my order.', timestamp: new Date().toISOString() },
  { messageId: 'app-store-preview-3', role: 'assistant', content: 'Sure — what would you like to know?', timestamp: new Date().toISOString() }
]

const MOCK_ACTIONS = [
  { name: 'app-store-preview-action-1', ui_button: 'Track my order', has_trigger: false },
  { name: 'app-store-preview-action-2', ui_button: 'Talk to a human', has_trigger: false, disabled: true }
]

const draft = ref('')
const messages = ref(DEFAULT_MESSAGES)

async function loadTranscript() {
  if (!props.appId) {
    messages.value = DEFAULT_MESSAGES
    return
  }
  try {
    const res = await getAppPreviewTranscript(props.appId)
    messages.value = res.messages?.length
      ? res.messages.map((m) => ({ messageId: m.id, role: m.role, content: m.content, timestamp: m.timestamp }))
      : DEFAULT_MESSAGES
  } catch {
    messages.value = DEFAULT_MESSAGES
  }
}

watch(() => props.appId, loadTranscript, { immediate: true })
</script>

<template>
  <div class="app-store-frozen-wrap">
    <div class="chat-window-shell" :class="{ 'app-store-frozen-lightened': loading }">
      <div class="chat-header">
        <div class="chat-header-icon"></div>
      </div>
      <div class="messages chat-body">
        <MessageBubble v-for="msg in messages" :key="msg.messageId" :message="msg" show-timestamp />
      </div>
      <div class="chat-footer">
        <ActionButtons :actions="MOCK_ACTIONS" :auto-tracking-enabled="false" />
        <ChatInput v-model="draft" disabled :recording="false" :mic-available="false" :talk-available="false" :audio-enabled="false" :spoken-text-enabled="false" />
      </div>
    </div>
    <div v-if="loading" class="app-store-frozen-spinner">
      <ProgressSpinner />
    </div>
  </div>
</template>

<style scoped>
.app-store-frozen-wrap {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

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

.app-store-frozen-lightened {
  filter: brightness(1.2) saturate(0.7);
  opacity: 0.6;
}

.app-store-frozen-spinner {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #4a6fa5;
  pointer-events: none;
}

.app-store-frozen-spinner svg {
  width: 32px;
  height: 32px;
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
