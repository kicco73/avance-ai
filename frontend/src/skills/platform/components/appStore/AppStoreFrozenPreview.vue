<script setup>
import { toRef } from 'vue'
import ChatView from '../../../../components/chat/ChatView.vue'
import { createSampleChatStore } from '../../sampleChatStore.js'

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
