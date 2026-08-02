<script setup>
import { renderMarkdown as renderMarkdownBase } from '../../markdown.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

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
  // "Label sessions" view only (see BenchmarkProjectView.vue) — the
  // live chat never shows this.
  showTimestamp: { type: Boolean, default: false },
  // "Label sessions" view only — whether the Signals row this
  // message's own evaluation produced has an expert-annotated
  // expected_values (see Signals.expected_values). Shows a small "this
  // bubble has a signal annotation" marker; the live chat never sets this.
  signalsAnnotated: { type: Boolean, default: false }
})

const emit = defineEmits(['resend'])

function getMessageText(msg) {
  // Se l'utente vuole vedere lo spoken text ed è disponibile, usiamo audioText.
  // In modalità normale (o se audioText è vuoto) usiamo il testo streaming normale in msg.content.
  if (props.spokenTextEnabled && msg.role === 'assistant' && msg.audioText) {
    return msg.audioText
  }
  return msg.content || ''
}

// Deliberately terse (HH:MM, no date/seconds) — see showTimestamp's own
// docstring: "molto sintetico, piccolo e poco invasivo".
function formatTimestamp(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// The "Signal labelled" tooltip on the (!) badge — the browser's native
// `title` attribute wasn't rendering reliably (see useFloatingTooltip's
// own docstring, first built for Inspector.vue's (?) icon).
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
        class="bubble"
        :class="[
          message.role === 'user' ? 'bubble-user' : 'bubble-assistant',
          message.failed ? 'bubble-failed' : ''
        ]"
      >
        <span v-html="renderMarkdown(getMessageText(message) || '...')" />
        <span
          v-if="signalsAnnotated"
          ref="annotationIconRef"
          class="bubble-annotation-icon"
          tabindex="0"
          @mouseenter="showAnnotationTooltip"
          @mouseleave="hideAnnotationTooltip"
          @focus="showAnnotationTooltip"
          @blur="hideAnnotationTooltip"
        >!</span>
        <Teleport to="body">
          <span
            v-if="signalsAnnotated && annotationTooltipVisible"
            class="bubble-annotation-tooltip-floating"
            :style="annotationTooltipStyle"
          >
            Signal labelled
          </span>
        </Teleport>
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
}

/* "Label sessions" view only — see the signalsAnnotated prop's own
   docstring. Same amber used elsewhere for "pay attention, this differs
   from the live default" (see Inspector's own .inspector-detail-badge-current). */
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
}

.bubble-failed {
  background: #c62828;
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
