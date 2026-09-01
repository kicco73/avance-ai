<script setup>
// Renders a real chat instance (not a mock) fed a static fake conversation, so a
// project's index.css "skin" can be previewed live, updated in place per keystroke.
//
// The CSS goes into chatStore.js's own single shared skin <style> element
// (setSkinCss) rather than a separate tag of this component's own — a
// second tag doesn't just risk the ordering fight that element's own
// docstring describes, it has no dependency on applyAspect, so Test mode's
// "Apply aspect" toggle had no effect on whatever it was showing. Sharing
// the one element means entering Test mode (which flips applyAspect off)
// clears it same as it would for the real skin.
//
// This component stays mounted (v-show, not v-if) through ProjectDesignPanel's
// own file switch (index.css vs. any other file) — so "unmount" alone isn't
// enough to know when this preview stops being the thing on screen. An IntersectionObserver
// on the root element tracks actual visibility instead: a display:none
// ancestor (from any v-show layer above) collapses this element's geometry,
// which the observer reports as non-intersecting. While invisible this
// leaves the shared element alone rather than clearing it — chatStore.js's
// own loadSkin remains the one place that decides what belongs there when
// this preview isn't contributing, so invalidateSkin() (a re-run of that
// decision) is what hands control back, not a direct clear here.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MessageBubble from '../../../chat/MessageBubble.vue'
import ActionButtons from '../../../chat/ActionButtons.vue'
import ChatInput from '../../../chat/ChatInput.vue'
import { setSkinCss, invalidateSkin } from '../../../../chatStore.js'

const props = defineProps({
  css: { type: String, default: '' },
  stateKey: { type: String, default: '' },
  projectName: { type: String, required: true }
})

const MOCK_MESSAGES = [
  { messageId: 'preview-1', role: 'assistant', content: 'Hi! How can I help you today?', timestamp: new Date().toISOString() },
  { messageId: 'preview-2', role: 'user', content: 'I have a question about my order.', timestamp: new Date().toISOString() },
  { messageId: 'preview-3', role: 'assistant', content: 'Sure — what would you like to know?', timestamp: new Date().toISOString() }
]

const MOCK_ACTIONS = [
  { name: 'preview-action-1', ui_button: 'Track my order', has_trigger: false },
  { name: 'preview-action-2', ui_button: 'Talk to a human', has_trigger: false }
]

const draft = ref('')
const rootEl = ref(null)
const visible = ref(false)

let observer = null

function syncStyle() {
  if (!visible.value) {
    // Reassert whatever the real skin (project's saved index.css, gated on
    // applyAspect) should be — this preview's draft no longer gets a say.
    invalidateSkin()
    return
  }
  // sessionId omitted: a design-time preview of the live draft has no
  // session to pin an asset url(...) to (see resolveCssAssetUrls's own docstring).
  setSkinCss(props.css, props.projectName)
}

watch([() => props.css, () => props.projectName, visible], syncStyle, { immediate: true })

onMounted(() => {
  // jsdom (unit tests) has no IntersectionObserver — fall back to "always
  // visible", the same unconditional-inject behavior this replaces, rather
  // than crashing or silently never showing the preview under test.
  if (typeof IntersectionObserver === 'undefined') {
    visible.value = true
    return
  }
  observer = new IntersectionObserver(([entry]) => { visible.value = entry.isIntersecting }, { threshold: 0 })
  if (rootEl.value) observer.observe(rootEl.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  if (visible.value) invalidateSkin()
})
</script>

<template>
  <div ref="rootEl" class="chat-window-shell" :class="stateKey ? `state-${stateKey}` : null" :data-state="stateKey || null">
    <div class="chat-header">
      <div class="chat-header-icon"></div>
    </div>
    <div class="messages chat-body">
      <MessageBubble v-for="msg in MOCK_MESSAGES" :key="msg.messageId" :message="msg" show-timestamp />
    </div>
    <div class="chat-footer">
      <ActionButtons :actions="MOCK_ACTIONS" disabled :auto-tracking-enabled="false" />
      <ChatInput v-model="draft" disabled :recording="false" :mic-available="false" :talk-available="false" :audio-enabled="false" :spoken-text-enabled="false" />
    </div>
  </div>
</template>

<style scoped>
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
