<script setup>
// The live chat's own full-viewport window — App.vue's one instance,
// shown either as a plain user's whole app or an admin's pushed 'chat'.
// Fixed/full-viewport so it can sit inside .app-body's shared perspective
// and participate in the admin push/pop flip transition; ChatView itself
// carries no opinion about that at all, since RunChat.vue's embedded Test
// chat uses the exact same ChatView as a normal contained flex item.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatView from './ChatView.vue'
import TermsView from '../TermsView.vue'
import SplashScreen from '../SplashScreen.vue'
import { getLegalTermsStatus, postAcceptProjectTerms } from '../../api.js'
import { loadMessages } from '../../chatStore.js'
import { onLiveSkinApplied } from '../../chatSkin.js'
import { setCanvasColor, restoreCanvasColor } from '../../canvasColor.js'

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

// Canvas-color sync (see canvasColor.js's own comment for why this
// exists at all): keeps <html>'s background-color matching .chat-footer's
// own, so the iOS strip WebKit paints with that color under the home
// indicator reads as a continuation of the skinned footer instead of a
// mismatched gap. Re-run on: mount, a live skin (re)applying (see
// chatSkin.js's onLiveSkinApplied), .chat-footer's own background-color
// transition finishing (not every intermediate frame — see
// onFooterTransitionEnd), and any DOM change inside this window
// (childList catches .chat-footer appearing once terms resolve;
// attributes/data-state catches an automaton state change, since a
// project's skin can key its footer color off .chat-window-shell's own
// [data-state]).
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
  // A skin that never sets .chat-footer's own background computes as
  // transparent — #ffffff is what the footer actually shows by default
  // in that case (see ChatView.vue's own .chat-footer, which sets no
  // background of its own either).
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

defineExpose({
  refreshProjectsMenu: () => chatViewRef.value?.refreshProjectsMenu()
})
</script>

<template>
  <div class="live-chat-window" ref="rootEl">
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
  top: 0;
  left: 0;
  right: 0;
  /* Extends past the viewport's own bottom edge on standalone iOS,
     where WebKit bug #301108 leaves a gap there otherwise — see
     index.html's own viewport meta comment and
     useVisualViewport.js's installViewportOvershoot(). 0px, a no-op,
     everywhere else (a plain browser tab, non-iOS, or once Apple fixes
     the bug). */
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
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
