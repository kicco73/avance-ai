<script setup>
import { nextTick, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  breaks: true,
  linkify: true,
  typographer: true,
  html: false
})

function renderMarkdown(text) {
  return DOMPurify.sanitize(md.render(text ?? ''))
}

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
            v-html="renderMarkdown(msg.content)"
          />
        </div>
      </TransitionGroup>

      <div
        v-if="loading"
        class="bubble bubble-assistant bubble-loading"
      >
        {{ status || '...' }}
      </div>
    </div>

    <div
      v-if="errorMessage"
      class="chat-error-row"
    >
      <p class="chat-error">
        {{ errorMessage }}
      </p>

      <button
        v-if="errorDetail"
        type="button"
        class="chat-error-details-btn"
        @click="showErrorDetail = !showErrorDetail"
      >
        {{ showErrorDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>

    <pre
      v-if="errorMessage && errorDetail && showErrorDetail"
      class="chat-error-detail"
    >{{ errorDetail }}</pre>

    <p
      v-if="finalStateReached"
      class="chat-ended-notice"
    >
      The conversation has ended.
    </p>

    <form
      class="input-row"
      @submit.prevent="submit"
    >
      <input
        ref="inputEl"
        v-model="draft"
        type="text"
        placeholder="Type a message..."
        :disabled="loading || finalStateReached"
      />

      <button
        type="submit"
        :disabled="loading || finalStateReached || !draft.trim()"
      >
        Send
      </button>
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
  animation: message-bubble-in 220ms
    cubic-bezier(0.34, 1.56, 0.64, 1) both;
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
  line-height: 1.5;
  overflow-wrap: anywhere;
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

/* ---------------- Markdown ---------------- */

.bubble :deep(p) {
  margin: 0 0 0.8rem;
}

.bubble :deep(p:last-child) {
  margin-bottom: 0;
}

.bubble :deep(h1),
.bubble :deep(h2),
.bubble :deep(h3),
.bubble :deep(h4),
.bubble :deep(h5),
.bubble :deep(h6) {
  margin: 0.8rem 0 0.5rem;
  line-height: 1.3;
}

.bubble :deep(h1:first-child),
.bubble :deep(h2:first-child),
.bubble :deep(h3:first-child),
.bubble :deep(h4:first-child) {
  margin-top: 0;
}

.bubble :deep(ul),
.bubble :deep(ol) {
  margin: 0.5rem 0;
  padding-left: 1.5rem;
}

.bubble :deep(li) {
  margin: 0.25rem 0;
}

.bubble :deep(blockquote) {
  margin: 0.75rem 0;
  padding: 0.2rem 0 0.2rem 1rem;
  border-left: 4px solid #bbb;
  color: #666;
}

.bubble :deep(hr) {
  border: none;
  border-top: 1px solid #ccc;
  margin: 1rem 0;
}

.bubble :deep(pre) {
  overflow-x: auto;
  margin: 0.75rem 0;
  padding: 0.9rem;
  border-radius: 8px;
  background: #1e1e1e;
  color: #f8f8f2;
}

.bubble :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 0;
}

.bubble :deep(code) {
  font-family: Consolas, Monaco, Menlo, monospace;
  font-size: 0.9em;
}

.bubble :deep(:not(pre) > code) {
  background: rgba(0, 0, 0, 0.08);
  padding: 0.12rem 0.35rem;
  border-radius: 4px;
}

.bubble-user :deep(:not(pre) > code) {
  background: rgba(255, 255, 255, 0.2);
}

.bubble :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75rem 0;
}

.bubble :deep(th),
.bubble :deep(td) {
  border: 1px solid #ccc;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.bubble :deep(th) {
  background: rgba(0, 0, 0, 0.05);
}

.bubble-user :deep(th) {
  background: rgba(255, 255, 255, 0.15);
}

.bubble :deep(img) {
  max-width: 100%;
  border-radius: 6px;
}

.bubble :deep(a) {
  color: inherit;
  text-decoration: underline;
}

.bubble :deep(strong) {
  font-weight: 600;
}

.bubble :deep(em) {
  font-style: italic;
}
</style>