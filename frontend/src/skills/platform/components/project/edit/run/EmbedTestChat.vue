<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import html2canvas from 'html2canvas'
import ChatView from '../../../../../../components/chat/ChatView.vue'
import RestartFromHereButton from '../../../../../../components/chat/RestartFromHereButton.vue'
import ToastContainer from '../../../../../../components/ToastContainer.vue'
import HumanTakeoverToasts from '../../../../../../components/HumanTakeoverToasts.vue'
import DialogHost from '../../../../../../components/DialogHost.vue'
import SplashScreen from '../../../../../../components/SplashScreen.vue'
import LoginView from '../../../../../../components/LoginView.vue'
import { needsLogin } from '../../../../../../authStore.js'
import { activeChatMode } from '../../../../../../chatSkin.js'
import { useAppBoot } from '../../../../../../composables/useAppBoot.js'
import { useViewStack } from '../../../../../../composables/useViewStack.js'
import { testStore } from '../../../../testChatStore.js'
import { transcriptOf } from './runTranscript.js'

const props = defineProps({
  projectId: { type: String, required: true },
  sessionId: { type: String, required: true }
})

const currentUserProfile = ref(null)
const currentUserRole = ref(null)
const landingProjectId = ref(null)
const viewStack = useViewStack(currentUserRole)

const { bootStatus, needsTerms, startBootSequence, handleLoggedIn } = useAppBoot(
  currentUserProfile, currentUserRole, landingProjectId, viewStack
)

function postToParent(type, messageId) {
  window.parent.postMessage({ source: 'run-chat-embed', type, messageId }, window.location.origin)
}

function onSelectMessage(message) {
  postToParent('select-message', message.messageId)
}

function reportAdvanced() {
  window.parent.postMessage({ source: 'run-chat-embed', type: 'run-advanced' }, window.location.origin)
}

function reportTranscript() {
  window.parent.postMessage(
    { source: 'run-chat-embed', type: 'run-transcript', ...transcriptOf(testStore) },
    window.location.origin
  )
}

watch(testStore.turnCount, reportAdvanced)
watch(testStore.signalValues, reportAdvanced)
watch(testStore.state, reportTranscript)
watch(testStore.messages, reportTranscript, { deep: true })

function backgroundImageUrl(el) {
  const match = getComputedStyle(el).backgroundImage.match(/url\(["']?(.*?)["']?\)/)
  return match ? match[1] : null
}

async function toDataUri(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

function markCrossOriginBackgrounds() {
  const marked = []
  for (const el of document.querySelectorAll('*')) {
    const url = backgroundImageUrl(el)
    if (!url || url.startsWith('data:')) continue
    let absolute
    try {
      absolute = new URL(url, document.baseURI)
    } catch {
      continue
    }
    if (absolute.origin === window.location.origin) continue
    el.dataset.snapshotBgId = String(marked.length)
    marked.push({ el, href: absolute.href })
  }
  return marked
}

async function inlineCrossOriginBackgrounds(clonedDocument, marked) {
  const cache = new Map()
  for (const { el, href } of marked) {
    if (!cache.has(href)) {
      try {
        const res = await fetch(href, { credentials: 'include' })
        cache.set(href, await toDataUri(await res.blob()))
      } catch {
        cache.set(href, null)
      }
    }
    const dataUri = cache.get(href)
    if (!dataUri) continue
    const clonedEl = clonedDocument.querySelector(`[data-snapshot-bg-id="${el.dataset.snapshotBgId}"]`)
    if (clonedEl) clonedEl.style.backgroundImage = `url("${dataUri}")`
  }
}

function pinModalDialog(clonedDocument) {
  const liveDialog = document.querySelector('dialog[open]')
  const clonedDialog = clonedDocument.querySelector('dialog')
  if (!liveDialog || !clonedDialog) return
  const rect = liveDialog.getBoundingClientRect()
  Object.assign(clonedDialog.style, {
    position: 'fixed', margin: '0', zIndex: '2147483647',
    top: `${rect.top}px`, left: `${rect.left}px`, width: `${rect.width}px`, height: `${rect.height}px`,
  })
  const backdrop = clonedDocument.createElement('div')
  backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:2147483646;'
  clonedDialog.before(backdrop)
}

async function captureSnapshot() {
  const marked = markCrossOriginBackgrounds()
  try {
    const canvas = await html2canvas(document.body, {
      backgroundColor: '#ffffff',
      onclone: async (clonedDocument) => {
        for (const shell of clonedDocument.querySelectorAll('.chat-window-shell')) {
          shell.style.animation = 'none'
        }
        pinModalDialog(clonedDocument)
        await inlineCrossOriginBackgrounds(clonedDocument, marked)
      },
    })
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
    window.parent.postMessage({ source: 'run-chat-embed', type: 'snapshot-captured', blob }, window.location.origin)
  } finally {
    for (const { el } of marked) delete el.dataset.snapshotBgId
  }
}

const expectedSessionId = ref(props.sessionId)
let restartResolve = null

async function restartSession() {
  const newSessionId = await new Promise((resolve) => {
    restartResolve = resolve
    testStore.handleNewSession()
  })
  window.parent.postMessage({ source: 'run-chat-embed', type: 'session-restarted', sessionId: newSessionId }, window.location.origin)
}

function onParentMessage(event) {
  if (event.origin !== window.location.origin) return
  if (event.data?.source !== 'run-chat-parent') return
  if (event.data.type === 'capture-snapshot') captureSnapshot()
  if (event.data.type === 'restart-session') restartSession()
}

let stopSessionWatch = null

onMounted(() => {
  activeChatMode.value = 'test'
  testStore.setProject(props.projectId)
  stopSessionWatch = watch(testStore.currentSessionId, (id) => {
    if (id == null) return
    if (restartResolve) {
      expectedSessionId.value = String(id)
      const resolve = restartResolve
      restartResolve = null
      resolve(id)
      return
    }
    if (String(id) !== expectedSessionId.value) testStore.selectSession({ id: expectedSessionId.value })
  })
  window.addEventListener('message', onParentMessage)
  startBootSequence()
})
onBeforeUnmount(() => {
  stopSessionWatch?.()
  window.removeEventListener('message', onParentMessage)
  testStore.clearChatUi()
})
</script>

<template>
  <ToastContainer />
  <HumanTakeoverToasts />
  <DialogHost />
  <div class="embed-chat-root">
    <LoginView v-if="needsLogin" @logged-in="handleLoggedIn" />
    <div v-else-if="needsTerms" class="embed-chat-notice">Open the main app and accept the terms first.</div>
    <SplashScreen v-else-if="bootStatus !== 'ready'" variant="connecting" />
    <ChatView
      v-else
      hide-sessions-panel
      :store="testStore"
      selectable
      @select-message="onSelectMessage"
    >
      <template #message-actions="{ message }">
        <RestartFromHereButton
          v-if="message.role === 'user'"
          @click="postToParent('restart-resend', message.messageId)"
          @double-click="postToParent('restart-prefill', message.messageId)"
        />
      </template>
    </ChatView>
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
