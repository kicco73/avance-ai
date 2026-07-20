<script setup>
import { nextTick, ref, watch } from 'vue'

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  },
  status: {
    type: String,
    default: ''
  },
  error: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['send', 'resend'])

const draft = ref('')
const scrollEl = ref(null)

function submit() {
  const text = draft.value.trim()
  if (!text || props.loading) return
  emit('send', text)
  draft.value = ''
}

function resend(i) {
  if (props.loading) return
  emit('resend', i)
}

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    }
  }
)
</script>

<template>
  <div class="chat-window">
    <div class="messages" ref="scrollEl">
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message-row"
        :class="msg.role === 'user' ? 'message-row-user' : 'message-row-assistant'"
      >
        <button
          v-if="msg.role === 'user' && msg.failed"
          type="button"
          class="resend-icon"
          title="Message not sent. Tap to retry."
          @click="resend(i)"
        >
          &#33;
        </button>
        <div
          class="bubble"
          :class="[
            msg.role === 'user' ? 'bubble-user' : 'bubble-assistant',
            msg.failed ? 'bubble-failed' : ''
          ]"
        >
          {{ msg.content }}
        </div>
      </div>
      <div v-if="loading" class="bubble bubble-assistant bubble-loading">{{ status || '...' }}</div>
    </div>

    <p class="chat-error" v-if="error">{{ error }}</p>

    <form class="input-row" @submit.prevent="submit">
      <input
        v-model="draft"
        type="text"
        placeholder="Type a message..."
        :disabled="loading"
      />
      <button type="submit" :disabled="loading || !draft.trim()">Send</button>
    </form>
  </div>
</template>

<style scoped>
.chat-window {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.message-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  max-width: 70%;
}

.message-row-user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-row-assistant {
  align-self: flex-start;
}

.bubble {
  max-width: 100%;
  padding: 0.6rem 0.9rem;
  border-radius: 12px;
  line-height: 1.4;
  white-space: pre-wrap;
}

.bubble-user {
  background: #4a6fa5;
  color: white;
  border-bottom-right-radius: 2px;
}

.bubble-assistant {
  background: #eee;
  color: #222;
  border-bottom-left-radius: 2px;
}

.bubble-failed {
  background: #c62828;
}

.bubble-loading {
  align-self: flex-start;
  font-style: italic;
  color: #888;
}

.resend-icon {
  flex: none;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 50%;
  border: none;
  background: #c62828;
  color: white;
  font-weight: bold;
  font-size: 0.9rem;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.resend-icon:hover {
  background: #a02020;
}

.chat-error {
  color: #c62828;
  padding: 0 1rem;
  font-size: 0.85rem;
}

.input-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid #ddd;
}

.input-row input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.95rem;
}

.input-row button {
  padding: 0.5rem 1.2rem;
  border-radius: 6px;
  border: none;
  background: #4a6fa5;
  color: white;
  cursor: pointer;
}

.input-row button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
