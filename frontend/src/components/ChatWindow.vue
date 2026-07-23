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
  errorMessage: {
    type: String,
    default: ''
  },
  errorDetail: {
    type: String,
    default: ''
  },
  finalStateReached: {
    type: Boolean,
    default: false
  },
  // True once the initial GET /api/messages hydration has settled (success
  // or failure). Gates the bump-in animation's CSS name: history arrives
  // asynchronously after this component has already mounted, so a bare
  // <TransitionGroup> without `appear` isn't enough on its own here — every
  // hydrated row would still read as "just added" the moment the fetch
  // resolves. Before that point the group uses Vue's unstyled default
  // transition name ('v'), which is a no-op with no matching CSS.
  historyLoaded: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['send', 'resend'])

const draft = ref('')
const scrollEl = ref(null)
const inputEl = ref(null)
const showErrorDetail = ref(false)

// Collapse the detail disclosure whenever a new/different error replaces
// the previous one, so it doesn't stay stuck open showing stale detail.
watch(
  () => props.errorMessage,
  () => {
    showErrorDetail.value = false
  }
)

function submit() {
  const text = draft.value.trim()
  if (!text || props.loading || props.finalStateReached) return
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

// The input gets disabled while a reply is in flight, which drops browser
// focus — reclaim it once the reply lands so the user isn't forced to click
// back into the field before every message.
watch(
  () => props.loading,
  async (isLoading, wasLoading) => {
    if (isLoading || !wasLoading || props.finalStateReached) return
    await nextTick()
    inputEl.value?.focus()
  }
)
</script>

<template>
  <div class="chat-window">
    <div class="messages" ref="scrollEl">
      <TransitionGroup :name="historyLoaded ? 'message-bubble' : 'v'">
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
      </TransitionGroup>
      <div v-if="loading" class="bubble bubble-assistant bubble-loading">{{ status || '...' }}</div>
    </div>

    <div class="chat-error-row" v-if="errorMessage">
      <p class="chat-error">{{ errorMessage }}</p>
      <button
        v-if="errorDetail"
        type="button"
        class="chat-error-details-btn"
        @click="showErrorDetail = !showErrorDetail"
      >
        {{ showErrorDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>
    <pre v-if="errorMessage && errorDetail && showErrorDetail" class="chat-error-detail">{{ errorDetail }}</pre>
    <p class="chat-ended-notice" v-if="finalStateReached">
      Final state reached — the conversation has ended.
    </p>

    <form class="input-row" @submit.prevent="submit">
      <input
        ref="inputEl"
        v-model="draft"
        type="text"
        placeholder="Type a message..."
        :disabled="loading || finalStateReached"
      />
      <button type="submit" :disabled="loading || finalStateReached || !draft.trim()">Send</button>
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

/* Fade-in + slight scale overshoot ("bump") for newly added message rows
   only — TransitionGroup applies this solely to rows entering after the
   initial mount, never to ones already present at mount time. */
@keyframes message-bubble-in {
  from {
    opacity: 0;
    transform: scale(0.92);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.message-bubble-enter-active {
  animation: message-bubble-in 220ms cubic-bezier(0.34, 1.56, 0.64, 1) both;
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

.chat-error-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0 1rem;
}

.chat-error {
  color: #c62828;
  font-size: 0.85rem;
  margin: 0;
}

.chat-error-details-btn {
  flex: none;
  border: none;
  background: none;
  color: #4a6fa5;
  font-size: 0.8rem;
  text-decoration: underline;
  cursor: pointer;
  padding: 0;
}

.chat-error-detail {
  margin: 0.3rem 1rem 0;
  padding: 0.5rem 0.75rem;
  background: #fdecea;
  color: #7a1f1f;
  font-size: 0.78rem;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-ended-notice {
  color: #444;
  background: #f5f5f7;
  margin: 0 1rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
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
