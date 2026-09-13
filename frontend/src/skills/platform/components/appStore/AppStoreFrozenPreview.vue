<script setup>
import { toRef } from 'vue'
import ChatView from '../../../../components/chat/ChatView.vue'
import { createSampleChatStore } from '../../sampleChatStore.js'

// The static teaser only: a real sample of what this app's chat looks
// like, frozen and inert. It used to double as the "session starting up"
// screen too, greying itself out around a spinner; that job belongs to
// ChatWaitingPanel now, which every kind of chat shares (see its own
// comment), and which simply veils whatever is underneath it, this
// included.
const props = defineProps({
  appId: { type: String, default: null }
})

const sampleStore = createSampleChatStore({ appId: toRef(props, 'appId') })
</script>

<template>
  <div class="app-store-frozen-wrap">
    <ChatView hide-sessions-panel :store="sampleStore" />
  </div>
</template>

<style scoped>
.app-store-frozen-wrap {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}
</style>
