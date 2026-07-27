<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { setApiError } from '../errorStore.js'
import { startRecording, stopRecording } from '../mic.js'

const md = new MarkdownIt({
  breaks: true,
  linkify: true,
  typographer: true,
  html: false
})

// The model sometimes emits a data:image URI directly in the reply text
// instead of wrapping it in markdown image syntax — with no `![]()` around
// it, markdown-it has nothing to recognize as an image and just renders it
// as a wall of base64 text. Wrap any such bare URI before rendering; one
// already inside `![]( ... )` is skipped (the negative lookbehind), so a
// well-behaved reply is untouched.
const BARE_DATA_IMAGE_RE = /(?<!\]\()(data:image\/[a-zA-Z0-9+.-]+;base64,[A-Za-z0-9+/=]+)/g

function autoWrapBareImages(text) {
  return text.replace(BARE_DATA_IMAGE_RE, '![]($1)')
}

function renderMarkdown(text) {
  return DOMPurify.sanitize(md.render(autoWrapBareImages(text ?? '')))
}

// Spoken-text mode only ever applies to the assistant, and only for a
// bubble that actually has a narrated phrase — a plain reply with no
// [audio] tag has nothing else to show, so it falls back to `content`.
function displayContent(msg, spokenTextEnabled) {
  if (spokenTextEnabled && msg.role === 'assistant' && msg.audioText) return msg.audioText
  return msg.content
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
  // Whether the CURRENT state accepts chat turns (see backend State.chat)
  // — when it doesn't, the backend rejects them too (see
  // ChatService._process_turn_locked).
  stateChat: {
    type: Boolean,
    default: true
  },
  historyLoaded: {
    type: Boolean,
    default: false
  },
  audioEnabled: {
    type: Boolean,
    default: false
  },
  // Whether the server actually has talk-service/listen-service
  // configured (see App.vue's boot ping) — false hides the corresponding
  // button entirely rather than disabling it, so the text input widens
  // into the freed space instead of showing a dead control.
  talkAvailable: {
    type: Boolean,
    default: true
  },
  micAvailable: {
    type: Boolean,
    default: true
  },
  // When on, assistant bubbles show audio_text (the short narrated
  // phrase) instead of their full content — a pure display switch, see
  // App.vue's toggleSpokenText.
  spokenTextEnabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['send', 'resend', 'toggle-audio', 'voice-message', 'toggle-spoken-text'])

const draft = ref('')
const scrollEl = ref(null)
const inputEl = ref(null)
const showErrorDetail = ref(false)
const recording = ref(false)

// Reaching a state with no further actions (final) does NOT stop the
// conversation on its own — only an explicit chat: false does.
const chatDisabled = computed(() => !props.stateChat)

watch(
  () => props.errorMessage,
  () => {
    showErrorDetail.value = false
  }
)

function submit() {
  const text = draft.value.trim()
  if (!text || props.loading || chatDisabled.value) return
  emit('send', text)
  draft.value = ''
}

function resend(i) {
  if (props.loading) return
  emit('resend', i)
}

// Push-to-talk: held down = recording, released = send. A plain right-
// click shouldn't start one. The transcribed text is never shown or
// staged in `draft` — see App.vue's handleVoiceMessage, which sends it
// straight through the normal turn flow, by design.
async function startPtt(event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  if (recording.value || props.loading || chatDisabled.value) return
  try {
    await startRecording()
    recording.value = true
  } catch (err) {
    setApiError('Microphone access was denied.', err.message)
  }
}

// Also bound to pointerleave/pointercancel (not just pointerup): a
// pointer that leaves the button's bounds while still held down must
// stop the recording too, never leave it listening indefinitely.
async function stopPtt() {
  if (!recording.value) return
  recording.value = false
  const blob = await stopRecording()
  if (blob?.size) emit('voice-message', blob)
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
    if (isLoading || !wasLoading || chatDisabled.value) return

    await nextTick()
    inputEl.value?.focus()
  }
)

// Exposed for App.vue: an action button click disables itself immediately
// (see ActionButtons' :disabled), which blurs it since it had focus — this
// lets the caller send focus back to the chat input once the action's
// response has landed.
function focusInput() {
  if (chatDisabled.value) return
  inputEl.value?.focus()
}

defineExpose({ focusInput })
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
            <span v-if="msg.transcribing">...</span>
            <span v-else v-html="renderMarkdown(displayContent(msg, spokenTextEnabled))" />
          </div>
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
      v-if="!stateChat"
      class="chat-ended-notice"
    >
      Please select:
    </p>

    <slot name="actions" />

    <form
      class="input-row"
      @submit.prevent="submit"
    >
      <input
        ref="inputEl"
        v-model="draft"
        type="text"
        placeholder="Type a message..."
        :disabled="loading || chatDisabled"
      />

      <button
        v-if="micAvailable"
        type="button"
        class="mic-btn"
        :class="{ 'mic-btn-recording': recording }"
        :disabled="!recording && (loading || chatDisabled)"
        :title="recording ? 'Release to send' : 'Hold to record a voice message'"
        @pointerdown.prevent="startPtt"
        @pointerup="stopPtt"
        @pointerleave="stopPtt"
        @pointercancel="stopPtt"
        @contextmenu.prevent
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z" />
          <path d="M17 11a1 1 0 1 0-2 0 3 3 0 0 1-6 0 1 1 0 1 0-2 0 5 5 0 0 0 4 4.9V18H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.1A5 5 0 0 0 17 11z" />
        </svg>
      </button>

      <button
        v-if="talkAvailable"
        type="button"
        class="audio-btn"
        :class="{ 'audio-btn-on': audioEnabled }"
        :title="audioEnabled ? 'Audio: On' : 'Audio: Off'"
        @click="emit('toggle-audio')"
      >
        <svg v-if="audioEnabled" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M3 9v6h4l5 5V4L7 9H3z" />
          <path d="M14.5 3.23v2.06c2.89 1.2 5 4.03 5 7.71s-2.11 6.51-5 7.71v2.06c4.01-1.28 7-5.09 7-9.77s-2.99-8.49-7-9.77zM16.5 12c0-1.77-.77-3.29-2-4.34v8.68c1.23-1.05 2-2.57 2-4.34z" />
        </svg>
        <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M3 9v6h4l5 5V4L7 9H3z" />
          <path d="M19.73 21 21 19.73l-9-9L4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.8L19.73 21zM19 12c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89 1.2 5 4.03 5 7.71zm-4.5-2.51v.13l2.44 2.44c.04-.28.06-.56.06-.85 0-1.77-.77-3.29-2-4.34-.16-.14-.33-.27-.5-.38z" />
        </svg>
      </button>

      <button
        v-if="talkAvailable"
        type="button"
        class="spoken-text-btn"
        :class="{ 'spoken-text-btn-on': spokenTextEnabled }"
        :title="spokenTextEnabled ? 'Showing spoken text' : 'Show spoken text'"
        @click="emit('toggle-spoken-text')"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M19 4H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7.5H9.5v-.5h-2v2h2v-.5H11V15c0 .55-.45 1-1 1H6c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5zm7 0h-1.5v-.5h-2v2h2v-.5H18V15c0 .55-.45 1-1 1h-4c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5z" />
        </svg>
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
  min-width: 0;
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
  margin: 0;
  padding: 0.5rem 1rem;
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

.mic-btn,
.audio-btn,
.spoken-text-btn {
  flex: none;
  width: 2.5rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mic-btn {
  touch-action: none;
  user-select: none;
}

.mic-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.mic-btn-recording {
  border-color: #c62828;
  background: #c62828;
  color: white;
  animation: mic-pulse 1.2s ease-in-out infinite;
}

.mic-btn-recording:hover {
  background: #a02020;
}

.mic-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.5); }
  50% { box-shadow: 0 0 0 6px rgba(198, 40, 40, 0); }
}

.audio-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.audio-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.audio-btn-on:hover:not(:disabled) {
  background: #256428;
}

.audio-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spoken-text-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.spoken-text-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.spoken-text-btn-on:hover:not(:disabled) {
  background: #256428;
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