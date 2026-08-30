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
  top: 0;
  left: 0;
  right: 0;
  /* height, not bottom: 0 (i.e. not inset: 0) — position: fixed's own
     inset: 0 doesn't reach the true bottom of the screen on this app's
     real deployment target (a standalone home-screen webapp on iOS);
     see SplashScreen.vue's own .splash, where this was first proven with
     nothing else on the page to blame. var(--real-viewport-height) is
     window.innerHeight itself, kept live by App.vue's own
     updateRealViewportHeight(). Side edges are padding — a device
     rendering edge-to-edge (see index.html's viewport-fit=cover) would
     otherwise clip content under a landscape notch/rounded corner.
     box-sizing so the padding shrinks the box instead of sitting outside
     it. Top and bottom aren't reserved here: SplashScreen/TermsView are
     their own position: fixed, centered overlays that never touch that
     edge anyway, and ChatView's .chat-header/.chat-footer reserve it
     themselves instead (see their own comments) — those are the
     elements a project's skin actually paints, so reserving the notch
     there lets a dark skin's own background extend behind it instead of
     showing this white fallback through a color-mismatched gap. */
  height: calc(var(--real-viewport-height, 100vh) + var(--safe-area-bottom));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  z-index: 100;
  display: flex;
  min-height: 0;
  min-width: 0;
  background: white;
}
</style>
