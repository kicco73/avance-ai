<script setup>
// Chronological, clickable message+transition list. Each timeline entry
// is { kind: 'message', message } or { kind: 'transition', transition,
// annotationStatus } — this component has no notion of mode of its own.
import { nextTick, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import { messageHasAnnotatedSignals } from '../../benchmarkTimeline.js'

const props = defineProps({
  timeline: { type: Array, required: true },
  signalsLog: { type: Array, default: () => [] },
  selected: { type: Object, default: null },
  // Forwarded to MessageBubble.vue; this component has no opinion of its
  // own on whether spoken text should show.
  spokenTextEnabled: { type: Boolean, default: false },
  // Whether the session was imported rather than played live — there's
  // no avance-computed state to compare an annotation against, so both
  // the transition badge and the signal marker read as a neutral
  // "labelled" tick instead of a correct/incorrect verdict.
  imported: { type: Boolean, default: false },
  // (stateKey) => displayLabel. A transition's old_state/new_state is
  // always the automaton's internal state key, never its human-facing
  // label — optional, since the two usually read the same.
  resolveStateLabel: { type: Function, default: null },
  // (stateKey, actionName) => displayLabel. Gates whether the self-loop
  // action badge renders at all (unlike resolveStateLabel, which always
  // renders something).
  resolveActionLabel: { type: Function, default: null },
  // Whether a `timeline` prop change should snap the view to the bottom.
  // Turn off when a change can mean an annotation/comment mid-review
  // rather than a new message, so users aren't scrolled away mid-edit.
  autoScroll: { type: Boolean, default: true },
  // Forwarded to MessageBubble.vue, same as spokenTextEnabled — this
  // component has no opinion of its own on what's available to react with.
  reactions: { type: Array, default: () => [] }
})

function stateLabel(stateKey) {
  return props.resolveStateLabel ? props.resolveStateLabel(stateKey) : stateKey
}

// Only meaningful for a self-loop: the state badge alone doesn't change,
// so this is the only hint of what action actually fired.
function actionLabel(transition) {
  return props.resolveActionLabel ? props.resolveActionLabel(transition.old_state, transition.action) : null
}

const emit = defineEmits(['select-message', 'select-transition', 'react'])

const rootEl = ref(null)

// This root is the actual scroll region, not whatever wraps it — a
// parent-driven auto-scroll would silently no-op if the parent overflows
// instead. timeline is always a fresh array, so a shallow watch suffices.
function scrollToBottom() {
  nextTick(() => {
    if (rootEl.value) rootEl.value.scrollTop = rootEl.value.scrollHeight
  })
}

watch(() => props.timeline, () => { if (props.autoScroll) scrollToBottom() })

function toBubbleMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, reaction: m.reaction, timestamp: m.timestamp }
}

function isMessageSelected(message) {
  return props.selected?.kind === 'message' && props.selected.message.id === message.id
}

function isTransitionSelected(transition) {
  return props.selected?.kind === 'transition' && props.selected.transition.id === transition.id
}

// A fired action that left the state unchanged still happened and is
// worth showing, just visually de-emphasized since nothing moved.
function isSelfLoop(transition) {
  return transition.old_state === transition.new_state
}
</script>

<template>
  <div class="chat-timeline" ref="rootEl">
    <template
      v-for="entry in timeline"
      :key="entry.kind + '-' + (entry.kind === 'message' ? entry.message.id : entry.transition.id)"
    >
      <div
        v-if="entry.kind === 'message'"
        class="timeline-row timeline-message-row"
        :class="[
          entry.message.role === 'user' ? 'timeline-message-row-user' : 'timeline-message-row-assistant',
          { 'timeline-row-selected': isMessageSelected(entry.message) }
        ]"
        @click="emit('select-message', entry.message)"
      >
        <span class="timeline-message-actions" @click.stop>
          <slot name="message-actions" :message="entry.message" />
        </span>
        <MessageBubble
          :message="toBubbleMessage(entry.message)"
          show-timestamp
          :spoken-text-enabled="spokenTextEnabled"
          :signals-annotated="messageHasAnnotatedSignals(entry.message, signalsLog)"
          :imported="imported"
          :reactions="reactions"
          @react="emit('react', entry.message.id, $event)"
        />
      </div>

      <div
        v-else
        class="timeline-row timeline-transition-row"
        :class="[
          { 'timeline-row-selected': isTransitionSelected(entry.transition), 'timeline-transition-row-self-loop': isSelfLoop(entry.transition) },
          entry.annotationStatus ? `timeline-transition-row-${entry.annotationStatus}` : ''
        ]"
        @click="emit('select-transition', entry.transition)"
      >
        <span
          class="timeline-transition-arrow"
          :title="isSelfLoop(entry.transition) ? 'No actual state change here' : ''"
        >{{ isSelfLoop(entry.transition) ? '↻' : '→' }}</span>
        <span
          v-if="isSelfLoop(entry.transition) && actionLabel(entry.transition)"
          class="timeline-transition-action-badge"
        >{{ actionLabel(entry.transition) }}</span>
        <span class="timeline-transition-badge">{{ stateLabel(entry.transition.new_state) }}</span>
        <span
          v-if="entry.annotationStatus === 'correct'"
          class="timeline-transition-annotation-icon timeline-transition-annotation-icon-correct"
          title="Matches the expert-annotated expected state"
        >✓</span>
        <span
          v-else-if="entry.annotationStatus === 'incorrect'"
          class="timeline-transition-annotation-icon timeline-transition-annotation-icon-incorrect"
          title="Differs from the expert-annotated expected state"
        >✕</span>
        <span
          v-else-if="entry.annotationStatus === 'labelled'"
          class="timeline-transition-annotation-icon timeline-transition-annotation-icon-labelled"
          title="Expert-labelled — no avance-computed state to compare against on an imported session"
        >✓</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.chat-timeline {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.timeline-row {
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  cursor: pointer;
}

/* display:contents so an empty slot contributes no box/spacing of its
   own — whatever the slot renders is responsible for its own margin. */
.timeline-message-actions {
  display: contents;
}

.timeline-row:hover {
  background: #f7f9fc;
}

.timeline-row-selected {
  background: #e3ebf7;
}

.timeline-message-row {
  justify-content: flex-start;
}

.timeline-message-row-user {
  justify-content: flex-end;
}

.timeline-message-row-assistant {
  justify-content: flex-start;
}

.timeline-transition-row {
  justify-content: center;
  align-items: center;
  gap: 0.5rem;
  background: #fbf3e6;
}

.timeline-transition-row:hover {
  background: #f6e9d2;
}

.timeline-transition-row.timeline-row-selected {
  background: #f0dcb0;
}

/* De-emphasized rather than hidden — the state genuinely didn't move. */
.timeline-transition-row-self-loop {
  opacity: 0.5;
}

/* Whether the expert-annotated expected_state agrees with what actually
   happened — lets a reviewer spot a mismatch at a glance. */
.timeline-transition-row-correct {
  background: #e8f5e9;
}

.timeline-transition-row-correct:hover {
  background: #dcefdd;
}

.timeline-transition-row-correct.timeline-row-selected {
  background: #c8e6c9;
}

.timeline-transition-row-incorrect {
  background: #fdecea;
}

.timeline-transition-row-incorrect:hover {
  background: #fbdedb;
}

.timeline-transition-row-incorrect.timeline-row-selected {
  background: #f5c6c2;
}

/* An imported session has nothing genuine to compare an annotation
   against — same green as -correct, but its own class keeps "labelled"
   distinct from "verified correct" in the markup. */
.timeline-transition-row-labelled {
  background: #e8f5e9;
}

.timeline-transition-row-labelled:hover {
  background: #dcefdd;
}

.timeline-transition-row-labelled.timeline-row-selected {
  background: #c8e6c9;
}

.timeline-transition-arrow {
  color: #8a6d3b;
  font-weight: 600;
}

.timeline-transition-badge {
  display: inline-block;
  padding: 0.15rem 0.7rem;
  border-radius: 999px;
  background: #4a6fa5;
  color: white;
  font-size: 0.78rem;
  font-weight: 600;
}

/* A self-loop's fired-action label, shown only where the state badge
   right after it can't say anything useful on its own. */
.timeline-transition-action-badge {
  display: inline-block;
  padding: 0.15rem 0.7rem;
  border-radius: 999px;
  background: #8a6d3b;
  color: white;
  font-size: 0.78rem;
  font-weight: 600;
}

.timeline-transition-annotation-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.2rem;
  height: 1.2rem;
  border-radius: 50%;
  font-size: 0.72rem;
  font-weight: 700;
  color: white;
}

.timeline-transition-annotation-icon-correct {
  background: #2e7d32;
}

.timeline-transition-annotation-icon-incorrect {
  background: #c62828;
}

.timeline-transition-annotation-icon-labelled {
  background: #2e7d32;
}
</style>
