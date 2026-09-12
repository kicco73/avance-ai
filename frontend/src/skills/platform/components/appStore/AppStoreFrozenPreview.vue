<script setup>
import { ref, watch } from 'vue'
import MessageBubble from '../../../../components/chat/MessageBubble.vue'
import ActionButtons from '../../../../components/chat/ActionButtons.vue'
import ChatInput from '../../../../components/chat/ChatInput.vue'
import { getAppPreviewTranscript } from '../../api.js'
import ChatWaitingPanel from '../../../../components/chat/ChatWaitingPanel.vue'
import { spokenTextEnabled } from '../../../../chatStoreFactory.js'
import { liveStore } from '../../../../chatStore.js'

// The static teaser only: a real sample of what this app's chat looks
// like, frozen and inert. It used to double as the "session starting up"
// screen too, greying itself out around a spinner — that job belongs to
// ChatWaitingPanel now, which every kind of chat shares (see its own
// comment), and which simply veils whatever is underneath it, this
// included.
const props = defineProps({
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
// The panel around this draws at once — it has the app from the list
// already — and only this part waits, on the same panel every other
// chat waits on. Before, the sample of whichever app was picked last
// stayed on screen until the new one's transcript arrived, which reads
// as the wrong app rather than as loading.
const loading = ref(false)

async function loadTranscript() {
  const asked = props.appId
  if (!asked) {
    messages.value = DEFAULT_MESSAGES
    return
  }
  loading.value = true
  try {
    const res = await getAppPreviewTranscript(asked)
    if (props.appId !== asked) return
    // Stale-response guard, the same one loadSkin has (see chatSkin.js):
    // picking another app while this one's transcript is in flight left
    // the late answer winning, and this card showing a conversation that
    // belongs to an app nobody had selected.
    messages.value = res.messages?.length
      ? res.messages.map((m) => ({ messageId: m.id, role: m.role, content: m.content, timestamp: m.timestamp }))
      : DEFAULT_MESSAGES
  } catch {
    if (props.appId === asked) messages.value = DEFAULT_MESSAGES
  } finally {
    if (props.appId === asked) loading.value = false
  }
}

watch(() => props.appId, loadTranscript, { immediate: true })
</script>

<template>
  <div class="app-store-frozen-wrap">
    <div class="chat-window-shell">
      <div class="chat-header">
        <div class="chat-header-icon"></div>
      </div>
      <div class="messages chat-body">
        <MessageBubble v-for="msg in messages" :key="msg.messageId" :message="msg" show-timestamp />
      </div>
      <div class="chat-footer">
        <ActionButtons :actions="MOCK_ACTIONS" :auto-tracking-enabled="false" />
        <ChatInput
          v-model="draft"
          disabled
          sample
          :store="liveStore"
        />
      </div>
    </div>
    <ChatWaitingPanel v-if="loading" />
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
