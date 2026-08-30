<script setup>
// The live chat's own window — App.vue's one instance, shown either as a
// plain user's whole app (a normal contained flex item, same as
// RunChat.vue's own embedded Test chat) or an admin's pushed 'chat' (App.vue
// wraps it in its own .chat-flip-layer there for the fixed/full-viewport
// treatment the 3D push/pop flip needs — see that class's own comment).
// ChatView itself carries no opinion about either usage at all.
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
  /* Not position: fixed — a plain flex item that just takes whatever
     space its parent gives it. An earlier version pinned this to
     window.visualViewport's own offset/height (top/height custom
     properties kept live by useVisualViewport()) specifically to dodge
     the on-screen keyboard: position: fixed tracks the *layout* viewport,
     which doesn't shrink or pan for the keyboard, stranding part of the
     window behind it. But that fix depended on visualViewport.offsetTop,
     which iOS has an active bug around (WebKit #259770 — it doesn't
     reliably settle right as the keyboard opens/closes), and on this
     element's containing block actually *being* the true viewport, which
     any transformed/perspective ancestor silently breaks — together they
     left a stray gap the keyboard-focus scroll could still open up.
     A plain flow element sidesteps all of that: nothing here needs
     repositioning when its own height changes, and the browser's own
     native keyboard-avoidance (shrinking the layout viewport on Android,
     scrolling the page on iOS) just carries it along like any other
     content instead of fighting it. The admin push/pop flip still needs
     a fixed, full-viewport layer to 3D-rotate — see .chat-flip-layer in
     App.vue, which wraps this component for that one usage only. */
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  /* Padding (not an explicit height) reserves the safe area — it must
     shrink the usable box, not sit outside whatever height flex gives
     it, so border-box is required here. Bottom is deliberately not
     reserved here too: only the footer actually touches that edge (see
     ChatInput.vue's own input-row), so reserving it twice would waste
     vertical space everywhere else in the window. */
  box-sizing: border-box;
  padding-top: env(safe-area-inset-top);
  padding-left: env(safe-area-inset-left);
  padding-right: env(safe-area-inset-right);
  background: white;
}
</style>
