<script setup>
import { ref, watch } from 'vue'
import ChatView from '../../../../components/chat/ChatView.vue'
import { getAppPreviewTranscript } from '../../api.js'
import { createSampleChatStore, SAMPLE_MESSAGES } from '../../sampleChatStore.js'

// The static teaser only: a real sample of what this app's chat looks
// like, frozen and inert — the same ChatView the conversation itself is,
// fed a sample store instead of a session, so what the store shows and
// what opens from it cannot drift apart. It used to double as the
// "session starting up" screen too, greying itself out around a spinner;
// that job belongs to ChatWaitingPanel now, which ChatView draws itself
// while this store's history hasn't landed.
const props = defineProps({
  appId: { type: String, default: null }
})

const messages = ref(SAMPLE_MESSAGES)
// The panel around this draws at once — it has the app from the list
// already — and only this part waits, on the same panel every other
// chat waits on. Before, the sample of whichever app was picked last
// stayed on screen until the new one's transcript arrived, which reads
// as the wrong app rather than as loading.
const sampleStore = createSampleChatStore({ messages })

async function loadTranscript() {
  const asked = props.appId
  if (!asked) {
    messages.value = SAMPLE_MESSAGES
    return
  }
  sampleStore.historyLoaded.value = false
  try {
    const res = await getAppPreviewTranscript(asked)
    if (props.appId !== asked) return
    // Stale-response guard, the same one loadSkin has (see chatSkin.js):
    // picking another app while this one's transcript is in flight left
    // the late answer winning, and this card showing a conversation that
    // belongs to an app nobody had selected.
    messages.value = res.messages?.length
      ? res.messages.map((m) => ({ id: m.id, messageId: m.id, role: m.role, content: m.content, timestamp: m.timestamp }))
      : SAMPLE_MESSAGES
  } catch {
    if (props.appId === asked) messages.value = SAMPLE_MESSAGES
  } finally {
    if (props.appId === asked) sampleStore.historyLoaded.value = true
  }
}

watch(() => props.appId, loadTranscript, { immediate: true })
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
