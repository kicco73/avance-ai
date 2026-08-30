<script setup>
// The live chat's own full-viewport window — App.vue's one instance,
// shown either as a plain user's whole app or an admin's pushed 'chat'.
// Fixed/full-viewport so it can sit inside .app-body's shared perspective
// and participate in the admin push/pop flip transition; ChatView itself
// carries no opinion about that at all, since RunChat.vue's embedded Test
// chat uses the exact same ChatView as a normal contained flex item.
import { ref, watch } from 'vue'
import ChatView from './ChatView.vue'
import TermsView from '../TermsView.vue'
import SplashScreen from '../SplashScreen.vue'
import { getLegalTermsStatus, postAcceptProjectTerms } from '../../api.js'
import { loadMessages } from '../../chatStore.js'

const props = defineProps({
  projectName: { type: String, required: true },
  hideSessionsPanel: { type: Boolean, default: false }
})

defineEmits(['project-select', 'project-download'])

const chatViewRef = ref(null)
const termsPending = ref(null)
const termsContent = ref('')
const checkFailed = ref(false)

async function checkTerms() {
  termsPending.value = null
  checkFailed.value = false
  try {
    const status = await getLegalTermsStatus(props.projectName)
    termsContent.value = status.content || ''
    termsPending.value = status.pending
    if (!status.pending) loadMessages()
  } catch {
    checkFailed.value = true
  }
}

async function fetchProjectTerms() {
  return { content: termsContent.value }
}

async function acceptTerms() {
  try {
    await postAcceptProjectTerms(props.projectName)
  } catch {
    return
  }
  termsPending.value = false
  loadMessages()
}

watch(() => props.projectName, checkTerms, { immediate: true })

defineExpose({
  refreshProjectsMenu: () => chatViewRef.value?.refreshProjectsMenu()
})
</script>

<template>
  <div class="live-chat-window">
    <SplashScreen v-if="checkFailed" variant="failed" @retry="checkTerms" />
    <SplashScreen v-else-if="termsPending === null" variant="connecting" />
    <TermsView
      v-if="termsPending"
      :show-reject="false"
      :fetch-terms="fetchProjectTerms"
      @accept="acceptTerms"
    />
    <ChatView
      v-else-if="termsPending === false"
      ref="chatViewRef"
      :hide-sessions-panel="hideSessionsPanel"
      @project-select="(name) => $emit('project-select', name)"
      @project-download="(name) => $emit('project-download', name)"
    />
  </div>
</template>

<style scoped>
.live-chat-window {
  position: fixed;
  left: 0;
  right: 0;
  /* top/height (not inset: 0): a fixed element's inset tracks the
     *layout* viewport, which doesn't shrink or pan for the on-screen
     keyboard or a pinch-zoom — the window then sat partly behind the
     keyboard, or off past a zoomed edge with html/body's own overflow:
     hidden leaving no way to scroll it back (iOS also doesn't reliably
     reset pageScale on blur, so this stuck until a reload). The custom
     properties are window.visualViewport's own offset/height, kept live
     by App.vue's useVisualViewport() call; 100dvh/0px are the fallback
     for a browser without that API. */
  top: var(--visual-viewport-offset-top, 0px);
  height: var(--visual-viewport-height, 100dvh);
  /* Padding (not the height/top above) reserves the safe area — it must
     shrink the usable box, not sit outside the height already computed
     from the visual viewport, so border-box is required here. Bottom is
     deliberately not reserved here too: only the footer actually touches
     that edge (see ChatInput.vue's own input-row), so reserving it twice
     would waste vertical space everywhere else in the window. */
  box-sizing: border-box;
  padding-top: env(safe-area-inset-top);
  padding-left: env(safe-area-inset-left);
  padding-right: env(safe-area-inset-right);
  z-index: 100;
  display: flex;
  min-height: 0;
  min-width: 0;
  background: white;
}
</style>
