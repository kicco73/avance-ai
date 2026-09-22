<script setup>
import { computed, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import ChatView from '../../../../../../components/chat/ChatView.vue'
import { holdSkin, invalidateSkin } from '../../../../../../chatSkin.js'
import { createSampleChatStore } from '../../../../sampleChatStore.js'
import { DraftSkinSource } from './draftSkinSource.js'

const props = defineProps({
  css: { type: String, default: '' },
  stateKey: { type: String, default: '' },
  reactionKey: { type: String, default: '' },
  projectId: { type: String, required: true }
})

const sampleStore = createSampleChatStore({
  stateKey: toRef(props, 'stateKey'), reactionKey: toRef(props, 'reactionKey'), appId: toRef(props, 'projectId')
})

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
