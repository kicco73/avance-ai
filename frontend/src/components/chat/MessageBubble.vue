<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { renderMarkdown as renderMarkdownBase } from '../../markdown.js'
import { toolTraceLine } from '../../toolTraceLine.js'
import { csvToMarkdownTable } from '../../toolResultTable.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'
import MessageReactionButton from './MessageReactionButton.vue'

const LONG_PRESS_MS = 450

const LONG_PRESS_MOVE_CANCEL_PX = 10

const BARE_DATA_IMAGE_RE = /(?<!\]\()(data:image\/[a-zA-Z0-9+.-]+;base64,[A-Za-z0-9+/=]+)/g

function autoWrapBareImages(text) {
  return text.replace(BARE_DATA_IMAGE_RE, '![]($1)')
}

function renderMarkdown(text) {
  if (!text) return ''
  return renderMarkdownBase(autoWrapBareImages(text))
}

const props = defineProps({
  message: { type: Object, required: true },
  spokenTextEnabled: { type: Boolean, default: false },
  showTimestamp: { type: Boolean, default: false },
  signalsAnnotated: { type: Boolean, default: false },
  imported: { type: Boolean, default: false },
  reactions: { type: Array, default: () => [] },
  invert: { type: Boolean, default: false }
})

const alignRole = computed(() => {
  if (!props.invert) return props.message.role
  return props.message.role === 'user' ? 'assistant' : 'user'
})

const emit = defineEmits(['resend', 'react'])

function reactionLabelFor(key) {
  return props.reactions.find((r) => r.key === key)?.ui_label ?? key
}

const bubbleRef = ref(null)
const reactionButtonRef = ref(null)
const longPressActive = ref(false)
let longPressTimer = null
let pressStartX = 0
let pressStartY = 0
let justOpenedReaction = false

function clearLongPressTimer() {
  if (longPressTimer != null) {
    clearTimeout(longPressTimer)
    longPressTimer = null
  }
}

function onBubblePointerDown(event) {
  if (props.message.role !== 'assistant' || !props.reactions.length) return
  pressStartX = event.clientX
  pressStartY = event.clientY
  longPressActive.value = true
  longPressTimer = setTimeout(() => {
    longPressTimer = null
    longPressActive.value = false
    justOpenedReaction = true
    reactionButtonRef.value?.open(bubbleRef.value)
  }, LONG_PRESS_MS)
}

function onBubblePointerMove(event) {
  if (longPressTimer == null) return
  const dx = event.clientX - pressStartX
  const dy = event.clientY - pressStartY
  if (Math.hypot(dx, dy) > LONG_PRESS_MOVE_CANCEL_PX) onBubblePointerEnd()
}

function onBubblePointerEnd() {
  longPressActive.value = false
  clearLongPressTimer()
}

function onBubbleClickCapture(event) {
  if (!justOpenedReaction) return
  justOpenedReaction = false
  event.preventDefault()
  event.stopPropagation()
}

onBeforeUnmount(clearLongPressTimer)

const isAwaitingReply = computed(() => props.message.role === 'assistant' && props.message.awaitingReply === true)

const isAwaitingText = computed(() => isAwaitingReply.value || props.message.transcribing === true)

const isPending = computed(() => props.message.role === 'assistant' && props.message.pending === true)

const showProgress = computed(() => {
  const percentage = props.message.progressPercentage
  return props.message.role === 'assistant' && percentage != null && percentage < 100
})

function getMessageText(msg) {
  if (props.spokenTextEnabled && msg.role === 'assistant' && msg.audioText) {
    return msg.audioText
  }
  return msg.content || ''
}

function formatTimestamp(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const {
  triggerRef: annotationIconRef,
  visible: annotationTooltipVisible,
  style: annotationTooltipStyle,
  show: showAnnotationTooltip,
  hide: hideAnnotationTooltip
} = useFloatingTooltip()
</script>

<template>
  <div
    v-if="!isPending"
    class="message-row"
    :class="alignRole === 'user' ? 'message-row-user' : 'message-row-assistant'"
  >
    <button
      v-if="message.role === 'user' && message.failed"
      type="button"
      class="resend-icon"
      title="Message not sent. Tap to retry."
      @click.stop="emit('resend')"
    >
      &#33;
    </button>

    <div class="bubble-col">
      <div
        ref="bubbleRef"
        class="bubble"
        :class="[
          alignRole === 'user' ? 'bubble-user' : 'bubble-assistant',
          message.failed ? 'bubble-failed' : '',
          {
            'bubble-bulging': longPressActive,
            'bubble-arriving': isAwaitingText,
            'bubble-reactable': message.role === 'assistant' && reactions.length
          }
        ]"
        @pointerdown="onBubblePointerDown"
        @pointermove="onBubblePointerMove"
        @pointerup="onBubblePointerEnd"
        @pointercancel="onBubblePointerEnd"
        @pointerleave="onBubblePointerEnd"
        @click.capture="onBubbleClickCapture"
      >
        <Transition name="tool-status-fade" mode="out-in">
          <div v-if="showProgress" key="progress" class="progress" role="progressbar" :aria-valuenow="message.progressPercentage" aria-valuemin="0" aria-valuemax="100">
            <div class="progress-title">{{ message.progressTitle }}</div>
            <div class="progress-track">
              <div class="progress-fill" :style="{ width: Math.max(0, Math.min(100, message.progressPercentage)) + '%' }"></div>
            </div>
          </div>
          <span v-else-if="isAwaitingReply && message.statusText" key="status" class="tool-status-text" aria-live="polite">
            {{ message.statusText }}
          </span>
          <span
            v-else-if="isAwaitingText"
            key="dots"
            class="typing-dots"
            :aria-label="message.role === 'user' ? 'Turning what you said into text' : 'Waiting for reply'"
          >
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
          </span>
          <span v-else key="content" v-html="renderMarkdown(getMessageText(message))" />
        </Transition>
        <Transition name="tool-status-fade">
          <span
            v-if="!isAwaitingReply && message.role === 'assistant' && message.statusText"
            class="tool-status-text tool-status-text-inline"
            aria-live="polite"
          >
            {{ message.statusText }}
          </span>
        </Transition>
        <div v-if="message.role === 'assistant' && message.toolCalls?.length" class="tool-call-trace">
          <details v-for="(call, idx) in message.toolCalls" :key="idx" class="tool-call-entry">
            <summary>{{ toolTraceLine(call) }}</summary>
            <pre class="tool-call-raw">{{ JSON.stringify({ arguments: call.arguments }, null, 2) }}</pre>
            <div
              v-if="csvToMarkdownTable(call.result)"
              class="tool-call-result-table"
              v-html="renderMarkdown(csvToMarkdownTable(call.result))"
            ></div>
            <pre v-else-if="call.result" class="tool-call-raw">{{ call.result }}</pre>
          </details>
        </div>
        <span
          v-if="signalsAnnotated"
          ref="annotationIconRef"
          class="bubble-annotation-icon"
          :class="{ 'bubble-annotation-icon-labelled': imported }"
          tabindex="0"
          @mouseenter="showAnnotationTooltip"
          @mouseleave="hideAnnotationTooltip"
          @focus="showAnnotationTooltip"
          @blur="hideAnnotationTooltip"
        >{{ imported ? '✓' : '!' }}</span>
        <Teleport to="body">
          <span
            v-if="signalsAnnotated && annotationTooltipVisible"
            class="bubble-annotation-tooltip-floating"
            :style="annotationTooltipStyle"
          >
            Signal labelled
          </span>
        </Teleport>

        <Transition name="reaction-badge-pop">
          <span
            v-if="message.role === 'user' && message.reaction"
            :key="message.reaction"
            class="reaction-badge"
            :title="reactionLabelFor(message.reaction)"
          >{{ reactionLabelFor(message.reaction) }}</span>
        </Transition>
        <span v-if="message.role === 'assistant'" class="reaction-badge-slot">
          <MessageReactionButton
            ref="reactionButtonRef"
            :reactions="reactions"
            :reaction="message.reaction"
            @save="emit('react', $event)"
          />
        </span>
      </div>
      <span v-if="showTimestamp" class="bubble-timestamp">{{ formatTimestamp(message.timestamp) }}</span>
    </div>
  </div>
</template>

<style scoped>
.message-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  max-width: 70%;
}

@media (max-width: 640px) {
  .message-row {
    max-width: 88%;
  }
}

.message-row-user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-row-assistant {
  align-self: flex-start;
}

.bubble-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.message-row-user .bubble-col {
  align-items: flex-end;
}

.message-row-assistant .bubble-col {
  align-items: flex-start;
}

.bubble-timestamp {
  margin-top: 0.15rem;
  font-size: 0.65rem;
  color: #999;
  padding: 0 0.2rem;
  user-select: none;
  -webkit-user-select: none;
}

.bubble {
  position: relative;
  max-width: 100%;
  padding: 0.6rem 0.9rem;
  border-radius: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  transition: transform 0.45s ease;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}

.bubble-bulging {
  transform: scale(1.035);
}

.bubble-reactable {
  cursor: pointer;
}

.bubble-annotation-icon {
  position: absolute;
  top: -0.4rem;
  right: -0.4rem;
  width: 1.1rem;
  height: 1.1rem;
  border-radius: 50%;
  background: #f5a623;
  color: #3a2600;
  font-size: 0.7rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1.5px solid white;
  cursor: help;
}

.bubble-annotation-icon-labelled {
  background: #2e7d32;
  color: white;
}

.bubble-annotation-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
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
  touch-action: pan-y;
}

.bubble-failed {
  background: #c62828;
}

.tool-call-trace {
  margin-top: 0.4rem;
  font-size: 0.72rem;
  opacity: 0.75;
}

.tool-call-entry summary {
  cursor: pointer;
  list-style: none;
}

.tool-call-entry summary::-webkit-details-marker {
  display: none;
}

.tool-call-entry summary::before {
  content: '🔍 ';
}

.tool-call-raw {
  margin: 0.3rem 0 0;
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.08);
  font-size: 0.68rem;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-call-result-table {
  margin-top: 0.3rem;
  font-size: 0.68rem;
}

.tool-call-result-table :deep(.md-table-wrap) {
  margin: 0;
}

.tool-call-result-table :deep(th),
.tool-call-result-table :deep(td) {
  padding: 0.2rem 0.35rem;
}

.tool-status-text {
  display: inline-block;
  font-style: italic;
  opacity: 0.7;
  padding: 0.15rem 0;
}

.tool-status-text-inline {
  display: block;
  margin-top: 0.3rem;
}

.tool-status-fade-enter-active,
.tool-status-fade-leave-active {
  transition: opacity 0.25s ease;
}

.progress {
  min-width: 12rem;
  padding: 0.1rem 0;
}

.progress-title {
  font-size: 0.85rem;
  margin-bottom: 0.4rem;
  opacity: 0.85;
}

.progress-track {
  position: relative;
  width: 100%;
  height: 0.4rem;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.bubble-user .progress-track {
  background: rgba(255, 255, 255, 0.25);
}

.progress-fill {
  height: 100%;
  border-radius: inherit;
  background: currentColor;
  transition: width 0.4s ease;
  background-image: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.35) 25%, transparent 25%,
    transparent 50%, rgba(255, 255, 255, 0.35) 50%,
    rgba(255, 255, 255, 0.35) 75%, transparent 75%, transparent
  );
  background-size: 1rem 1rem;
  animation: progress-fill-stripes 1s linear infinite;
}

@keyframes progress-fill-stripes {
  from {
    background-position: 1rem 0;
  }
  to {
    background-position: 0 0;
  }
}

.bubble-arriving {
  animation: bubble-arriving-fade-in 0.25s ease;
}

@keyframes bubble-arriving-fade-in {
  from {
    opacity: 0;
    transform: translateY(0.25rem);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.tool-status-fade-enter-from,
.tool-status-fade-leave-to {
  opacity: 0;
}

.typing-dots {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.15rem 0;
}

.typing-dot {
  width: 0.4rem;
  height: 0.4rem;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.4;
  animation: typing-dot-bounce 1.2s infinite ease-in-out;
}

.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing-dot-bounce {
  0%, 60%, 100% {
    opacity: 0.4;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-0.15rem);
  }
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

.reaction-badge-slot {
  position: absolute;
  bottom: -0.5rem;
  right: -0.35rem;
  z-index: 2;
}

.reaction-badge {
  position: absolute;
  bottom: -0.3rem;
  left: -0.3rem;
  z-index: 2;
  background: transparent;
  font-size: 1rem;
  line-height: 1;
}

.reaction-badge-pop-enter-active {
  animation: reaction-badge-bump 0.4s ease-out;
}

@keyframes reaction-badge-bump {
  0% {
    opacity: 0;
    transform: scale(0.3);
  }
  60% {
    opacity: 1;
    transform: scale(1.25);
  }
  100% {
    opacity: 1;
    transform: scale(1);
  }
}

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

.bubble :deep(.md-table-wrap) {
  overflow-x: auto;
  margin: 0.75rem 0;
}

.bubble :deep(table) {
  width: 100%;
  min-width: max-content;
  border-collapse: collapse;
  margin: 0;
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
  -webkit-user-drag: none;
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
