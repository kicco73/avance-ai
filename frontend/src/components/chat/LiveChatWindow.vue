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
  // null for a plain user whose account has no project to land on at all
  // (see useAppBoot.js's own resolveLandingView — this is what
  // getActiveProjectName resolves to when the system has zero projects).
  // Real, always non-null for the admin's own pushed 'chat' instance,
  // which only ever opens from an explicit row click.
  projectName: { type: String, default: null },
  hideSessionsPanel: { type: Boolean, default: false }
})

defineEmits(['project-select', 'project-download'])

const chatViewRef = ref(null)
const termsPending = ref(null)
const termsContent = ref('')
const checkFailed = ref(false)

async function checkTerms() {
  // No project to check terms for at all — see the projectName prop's
  // own comment. Leaves termsPending/checkFailed alone (both still their
  // initial null/false), so none of the other branches below render
  // either; the template's own !projectName check is what actually shows
  // something for this case.
  if (!props.projectName) return
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
    <SplashScreen v-if="!projectName" variant="no-project" />
    <SplashScreen v-else-if="checkFailed" variant="failed" @retry="checkTerms" />
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
  inset: 0;
  /* Side edges only — a device rendering edge-to-edge (see index.html's
     viewport-fit=cover) would otherwise clip content under a landscape
     notch/rounded corner. box-sizing so the padding shrinks the box
     instead of sitting outside it. Top and bottom aren't reserved here:
     SplashScreen/TermsView are their own position: fixed, centered
     overlays that never touch that edge anyway, and ChatView's
     .chat-header/.chat-footer reserve it themselves instead (see their
     own comments) — those are the elements a project's skin actually
     paints, so reserving the notch there lets a dark skin's own
     background extend behind it instead of showing this white fallback
     through a color-mismatched gap. */
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
