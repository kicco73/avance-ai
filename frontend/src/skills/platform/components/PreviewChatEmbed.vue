<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatView from '../../../components/chat/ChatView.vue'
import ToastContainer from '../../../components/ToastContainer.vue'
import DialogHost from '../../../components/DialogHost.vue'
import SplashScreen from '../../../components/SplashScreen.vue'
import LoginView from '../../../components/LoginView.vue'
import { needsLogin } from '../../../authStore.js'
import { holdSkin } from '../../../chatSkin.js'
import { useAppBoot } from '../../../composables/useAppBoot.js'
import { appStorePreviewStore, setPreviewApp, currentSessionId } from '../appStorePreviewStore.js'
import { AppSkinSource } from '../appSkinSource.js'

const props = defineProps({
  projectId: { type: String, required: true },
  sessionId: { type: String, required: true }
})

const currentUserProfile = ref(null)
const currentUserRole = ref(null)
const landingProjectId = ref(null)
const pushedView = ref(null)
const chatOpen = ref(false)
const showProfile = ref(false)
const navDirection = ref('forward')

const { bootStatus, needsTerms, startBootSequence, handleLoggedIn } = useAppBoot(
  currentUserProfile, currentUserRole, landingProjectId, pushedView, chatOpen, showProfile, navDirection
)

const releaseSkin = holdSkin(new AppSkinSource(computed(() => props.projectId)))

let stopSessionWatch = null

onMounted(() => {
  setPreviewApp(props.projectId)
  stopSessionWatch = watch(currentSessionId, (id) => {
    if (id == null || String(id) === props.sessionId) return
    appStorePreviewStore.selectSession({ id: props.sessionId })
  }, { immediate: true })
  startBootSequence()
})
onBeforeUnmount(() => {
  stopSessionWatch?.()
  releaseSkin()
  appStorePreviewStore.clearChatUi()
})
</script>

<template>
  <ToastContainer />
  <DialogHost />
  <div class="embed-chat-root">
    <LoginView v-if="needsLogin" @logged-in="handleLoggedIn" />
    <div v-else-if="needsTerms" class="embed-chat-notice">Open the main app and accept the terms first.</div>
    <SplashScreen v-else-if="bootStatus !== 'ready'" variant="connecting" />
    <ChatView v-else hide-sessions-panel :store="appStorePreviewStore" />
  </div>
</template>

<style scoped>
.embed-chat-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-height: 0;
}

.embed-chat-notice {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 1rem;
  text-align: center;
  color: #666;
  font-size: 0.9rem;
}
</style>
