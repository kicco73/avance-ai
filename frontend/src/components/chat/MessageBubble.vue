<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { renderMarkdown as renderMarkdownBase } from '../../markdown.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'
import MessageReactionButton from './MessageReactionButton.vue'

// How long a press on an assistant bubble must hold before the reaction
// picker opens — kept in sync with .bubble-bulging's own transition
// duration below, so the "inflate" finishes right as the picker appears.
const LONG_PRESS_MS = 450

// A press that drifts more than this before LONG_PRESS_MS elapses reads
// as a scroll/pan, not a hold — cancels the timer instead of opening the
// picker (touch-action stays pan-y, see .bubble-assistant below, so the
// browser's own vertical scroll runs concurrently with this timer; this
// threshold is what keeps a drifting scroll from also firing a reaction).
const LONG_PRESS_MOVE_CANCEL_PX = 10

const BARE_DATA_IMAGE_RE = /(?<!\]\()(data:image\/[a-zA-Z0-9+.-]+;base64,[A-Za-z0-9+/=]+)/g

function autoWrapBareImages(text) {
  return text.replace(BARE_DATA_IMAGE_RE, '![]($1)')
}

// Chat-specific: wraps bare pasted image data URIs as markdown images
// before handing off to the shared renderer (see ../markdown.js).
function renderMarkdown(text) {
  if (!text) return ''
  return renderMarkdownBase(autoWrapBareImages(text))
}

const props = defineProps({
  message: { type: Object, required: true },
  spokenTextEnabled: { type: Boolean, default: false },
  showTimestamp: { type: Boolean, default: false },
  // Whether this message's evaluation has an expert-annotated
  // expected_values; shows a small signal-annotation marker.
  signalsAnnotated: { type: Boolean, default: false },
  // Whether this belongs to an imported session — there's no real
  // avance-computed value to compare an annotation against, so the
  // marker reads as a neutral "labelled" tick instead of amber "!".
  imported: { type: Boolean, default: false },
  // {key, ui_label}[] — the active project's whole reaction vocabulary
  // (see chatStore.js's state.reactions). Empty disables long-press-to-react
  // entirely (see onBubblePointerDown's own guard below).
  reactions: { type: Array, default: () => [] }
})

const emit = defineEmits(['resend', 'react'])

function reactionLabelFor(key) {
  return props.reactions.find((r) => r.key === key)?.ui_label ?? key
}

// Long-press-to-react on an assistant bubble — replaces a dedicated
// trigger button entirely (see MessageReactionButton.vue's own docstring).
const bubbleRef = ref(null)
const reactionButtonRef = ref(null)
const longPressActive = ref(false)
let longPressTimer = null
let pressStartX = 0
let pressStartY = 0
// Set the instant the long-press fires and cleared on the next click —
// suppresses the ghost click that follows the closing touchend so a link
// under the finger doesn't also navigate (see onBubbleClickCapture).
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

// A finger drifting toward a scroll shouldn't also open the picker —
// cancels the pending long-press once the move exceeds the threshold,
// leaving the browser's own touch-action: pan-y scroll to keep running.
function onBubblePointerMove(event) {
  if (longPressTimer == null) return
  const dx = event.clientX - pressStartX
  const dy = event.clientY - pressStartY
  if (Math.hypot(dx, dy) > LONG_PRESS_MOVE_CANCEL_PX) onBubblePointerEnd()
}

// Shared by pointerup/pointercancel/pointerleave — any of them ends the
// press early, same "didn't hold long enough" outcome either way.
function onBubblePointerEnd() {
  longPressActive.value = false
  clearLongPressTimer()
}

// Capture-phase so it runs before a link's own click handler (navigation)
// fires — swallows exactly the one click that immediately follows the
// picker opening, then gets out of the way.
function onBubbleClickCapture(event) {
  if (!justOpenedReaction) return
  justOpenedReaction = false
  event.preventDefault()
  event.stopPropagation()
}

onBeforeUnmount(clearLongPressTimer)

// True while an assistant bubble's content is still empty (before the
// first streamed chunk lands) — shows animated dots instead of the reply.
const isAwaitingReply = computed(() => props.message.role === 'assistant' && !getMessageText(props.message))

function getMessageText(msg) {
  // Prefer spoken text (audioText) over the normal streamed content when enabled and available.
  if (props.spokenTextEnabled && msg.role === 'assistant' && msg.audioText) {
    return msg.audioText
  }
  return msg.content || ''
}

// Deliberately terse: HH:MM only, no date or seconds.
function formatTimestamp(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// The "Signal labelled" tooltip on the (!) badge.
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
    class="message-row"
    :class="message.role === 'user' ? 'message-row-user' : 'message-row-assistant'"
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
          message.role === 'user' ? 'bubble-user' : 'bubble-assistant',
          message.failed ? 'bubble-failed' : '',
          {
            'bubble-bulging': longPressActive,
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
        <span v-if="isAwaitingReply" class="typing-dots" aria-label="Waiting for reply">
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
        </span>
        <span v-else v-html="renderMarkdown(getMessageText(message))" />
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

        <!-- WhatsApp-style: a small badge hanging off the bubble's own
             bottom-right corner, not a separate control beside it. -->
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

/* Narrow phones can't spare 30% of the screen to whitespace — bubbles
   grow to near-full-width, same convention WhatsApp/iMessage use, with
   role still read from color/alignment rather than empty space. */
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
}

.bubble {
  position: relative;
  max-width: 100%;
  padding: 0.6rem 0.9rem;
  border-radius: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  /* Matches LONG_PRESS_MS above, so the bulge finishes growing right as
     the reaction picker opens. */
  transition: transform 0.45s ease;
  user-select: none;
  -webkit-user-select: none;
  /* iOS's own long-press callout (copy/share/lookup menu) is a separate
     mechanism from text selection — user-select: none alone doesn't
     suppress it, and it's exactly the kind of thing that competes with
     the long-press-to-react gesture below. */
  -webkit-touch-callout: none;
}

/* Visual feedback while holding down an assistant bubble, building up to
   the reaction picker opening (see onBubblePointerDown). */
.bubble-bulging {
  transform: scale(1.035);
}

/* Hints this bubble is press-and-hold interactive — only when there's
   actually a reaction vocabulary to react with. */
.bubble-reactable {
  cursor: pointer;
}

/* Amber = "pay attention, this differs from the live default". */
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

/* An imported session has no avance ground truth to be "wrong" against —
   a green tick ("labelled") instead of the amber "!" above. */
.bubble-annotation-icon-labelled {
  background: #2e7d32;
  color: white;
}

/* Teleported to <body>, position: fixed — see useFloatingTooltip.js. */
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
  /* pan-y (not none): keeps the container's vertical scroll working
     under a held finger — onBubblePointerMove cancels the pending
     long-press once a drag reads as a scroll rather than a hold, so the
     two gestures don't fight over the same touch. Horizontal panning
     stays constrained by this same ancestor rule (see the pre/table
     overflow-x containers below, reachable by mouse/trackpad drag and
     pinch-zoom, if not by a touch drag). */
  touch-action: pan-y;
}

.bubble-failed {
  background: #c62828;
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

/* WhatsApp-style: MessageReactionButton's own picker hangs off the
   bubble's bottom-right corner — .bubble is already position: relative,
   so this just needs its own absolute offset. */
.reaction-badge-slot {
  position: absolute;
  bottom: -0.5rem;
  right: -0.35rem;
  z-index: 2;
}

/* The bot's own reaction to a user message — read-only, icon only, no
   circle/background. Opposite corner from .reaction-badge-slot above, so
   the two never visually collide when both are on screen at once. */
.reaction-badge {
  position: absolute;
  bottom: -0.3rem;
  left: -0.3rem;
  z-index: 2;
  background: transparent;
  font-size: 1rem;
  line-height: 1;
}

/* Fade + bump on arrival — a brief overshoot past full size, then settle,
   rather than a plain fade. Only on enter: a cleared reaction just vanishes. */
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

/* Wraps every table (see markdown.js's table_open/table_close rules) —
   scrolls wide tables horizontally instead of squeezing columns until
   words split. */
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
  /* Safari's native drag-to-copy/share on an image is its own gesture,
     separate from user-select/-webkit-touch-callout above — same
     long-press-to-react conflict, just image-specific. */
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
