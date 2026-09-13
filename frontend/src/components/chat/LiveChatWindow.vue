<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatView from './ChatView.vue'
import TermsView from '../TermsView.vue'
import SplashScreen from '../SplashScreen.vue'
import { getLegalTermsStatus, postAcceptProjectTerms } from '../../api.js'
import { blockedReason, historyLoaded, loadMessages } from '../../chatStore.js'
import { onLiveSkinApplied } from '../../chatSkin.js'
import { setCanvasColor, restoreCanvasColor } from '../../canvasColor.js'

const props = defineProps({
  projectId: { type: String, default: null },
  hideSessionsPanel: { type: Boolean, default: false },
  role: { type: String, default: null },
  profile: { type: Object, default: null }
})

defineEmits(['project-select', 'project-download', 'manage-projects', 'home', 'profile', 'logout'])

const termsPending = computed(() => blockedReason.value === 'terms')

async function fetchProjectTerms() {
  const status = await getLegalTermsStatus(props.projectId)
  return { content: status.content || '' }
}

async function acceptTerms() {
  try {
    await postAcceptProjectTerms(props.projectId)
  } catch {
    return
  }
  loadMessages(props.projectId)
}

watch(() => props.projectId, (projectId) => loadMessages(projectId), { immediate: true })

const rootEl = ref(null)
let previousCanvasColor = ''
let observedFooterEl = null
let unregisterSkinApplied = null
let domObserver = null

function onFooterTransitionEnd(event) {
  if (event.propertyName === 'background-color') syncCanvasColor()
}

function syncCanvasColor() {
  const footerEl = rootEl.value?.querySelector('.chat-footer')
  if (!footerEl) return
  if (footerEl !== observedFooterEl) {
    observedFooterEl?.removeEventListener('transitionend', onFooterTransitionEnd)
    footerEl.addEventListener('transitionend', onFooterTransitionEnd)
    observedFooterEl = footerEl
  }
  const color = getComputedStyle(footerEl).backgroundColor
  setCanvasColor(color === 'rgba(0, 0, 0, 0)' ? '#ffffff' : color)
}

onMounted(() => {
  previousCanvasColor = document.documentElement.style.backgroundColor
  syncCanvasColor()
  unregisterSkinApplied = onLiveSkinApplied(syncCanvasColor)
  domObserver = new MutationObserver(syncCanvasColor)
  domObserver.observe(rootEl.value, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['data-state']
  })
})

onBeforeUnmount(() => {
  domObserver?.disconnect()
  unregisterSkinApplied?.()
  observedFooterEl?.removeEventListener('transitionend', onFooterTransitionEnd)
  restoreCanvasColor(previousCanvasColor)
})

</script>

<template>
  <div class="live-chat-window" ref="rootEl">
    <SplashScreen v-if="!projectId" variant="no-project" />
    <TermsView
      v-else-if="termsPending"
      :show-reject="false"
      :fetch-terms="fetchProjectTerms"
      @accept="acceptTerms"
    />
    <SplashScreen v-else-if="!historyLoaded" variant="connecting" />
    <ChatView
      v-else
      :hide-sessions-panel="hideSessionsPanel"
      :role="role"
      :profile="profile"
      @project-select="(name) => $emit('project-select', name)"
      @project-download="(name) => $emit('project-download', name)"
      @manage-projects="$emit('manage-projects')"
      @home="$emit('home')"
      @profile="$emit('profile')"
      @logout="$emit('logout')"
    />
  </div>
</template>

<style scoped>
.live-chat-window {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  z-index: 100;
  display: flex;
  min-height: 0;
  min-width: 0;
  background: white;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}
</style>
