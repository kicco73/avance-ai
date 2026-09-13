<script setup>
// The real chat window (ChatView.vue, the very component the live and
// Run chats are), fed a sample conversation instead of a session, so
// a project's index.css "skin" is previewed against the DOM it will
// actually meet — updated in place per keystroke.
//
// The CSS goes into chatSkin.js's own single shared skin <style> element
// (it holds it while visible, see holdSkin) rather than a separate tag of
// this component's own — a second tag doesn't just risk the ordering
// fight that element's own docstring describes, it has no dependency on
// applyAspect, so Test mode's "Apply aspect" toggle had no effect on
// whatever it was showing. Sharing the one element means entering Test
// mode (which flips applyAspect off) clears it same as it would for the
// real skin.
//
// This component stays mounted (v-show, not v-if) through ProjectDesignPanel's
// own file switch (index.css vs. any other file) — so "unmount" alone isn't
// enough to know when this preview stops being the thing on screen. An IntersectionObserver
// on the root element tracks actual visibility instead: a display:none
// ancestor (from any v-show layer above) collapses this element's geometry,
// which the observer reports as non-intersecting. Going invisible hands
// the element back to whoever owns it otherwise, rather than clearing it.
import { computed, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import ChatView from '../../../../../../components/chat/ChatView.vue'
import { holdSkin, invalidateSkin } from '../../../../../../chatSkin.js'
import { createSampleChatStore } from '../../../../sampleChatStore.js'
import { DraftSkinSource } from './draftSkinSource.js'

const props = defineProps({
  css: { type: String, default: '' },
  stateKey: { type: String, default: '' },
  projectId: { type: String, required: true }
})

const sampleStore = createSampleChatStore({ stateKey: toRef(props, 'stateKey') })

const rootEl = ref(null)
const visible = ref(false)

let observer = null
let releaseSkin = null

const draftSkin = new DraftSkinSource(
  computed(() => props.css),
  computed(() => props.projectId)
)

watch(visible, (isVisible) => {
  if (isVisible) {
    releaseSkin = holdSkin(draftSkin)
    return
  }
  releaseSkin?.()
  releaseSkin = null
})

watch([() => props.css, () => props.projectId], invalidateSkin)

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
  releaseSkin?.()
})
</script>

<template>
  <div ref="rootEl" class="chat-preview">
    <ChatView hide-sessions-panel :store="sampleStore" />
  </div>
</template>

<style scoped>
.chat-preview {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}
</style>
