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
  inset: 0;
  z-index: 100;
  display: flex;
  min-height: 0;
  min-width: 0;
}
</style>
