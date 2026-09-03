<script setup>
// Shared top-bar shell for every full-screen view (Manage projects, Edit
// project, Label sessions, Profile, Live chat) — one source for the
// border/padding/safe-area treatment instead of each view re-deriving its
// own (see the *-header rules this replaced in each of those files).
// `left`/`center`/`right` are plain slots so each view keeps its own
// controls and logic; only the shell itself, plus the shared
// .app-header-icon-btn/.app-header-title look (exported unscoped below,
// since slot content renders in the parent's own scope, out of reach of a
// scoped selector here), is centralized.
import { ref } from 'vue'

const props = defineProps({
  // 'solid': the white, bordered bar every non-chat view uses.
  // 'overlay': transparent and absolutely positioned over its parent
  // (LiveChatWindow's own skinned .chat-header), so a project's skin
  // still shows through underneath the controls.
  variant: { type: String, default: 'solid' }
})

const rootEl = ref(null)

// ManageProjectsView's own ResizeObserver needs the real header element
// (to measure the same content-box width its layout math already assumed)
// — a template ref on this component would otherwise only ever resolve to
// the component instance, not the DOM node.
defineExpose({ el: rootEl })
</script>

<template>
  <header ref="rootEl" class="app-header" :class="`app-header-${props.variant}`">
    <div class="app-header-left"><slot name="left" /></div>
    <div class="app-header-center"><slot name="center" /></div>
    <div class="app-header-right"><slot name="right" /></div>
  </header>
</template>

<style scoped>
.app-header {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 0.6rem;
  flex-shrink: 0;
}

.app-header-solid {
  padding: calc(0.75rem + var(--safe-area-top)) 1rem 0.75rem;
  border-bottom: 1px solid #ddd;
  background: white;
}

/* Live chat: no border/background of its own — the project's skin (see
   ChatView.vue's own .chat-header) paints that instead, and this just
   overlays its controls on top of it. */
.app-header-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 20;
  padding: calc(0.75rem + var(--safe-area-top)) calc(0.75rem + var(--safe-area-right)) 0.75rem calc(0.75rem + var(--safe-area-left));
}

.app-header-left,
.app-header-right {
  display: flex;
  align-items: center;
  min-width: 0;
}

.app-header-left {
  gap: 0.6rem;
  justify-self: start;
}

.app-header-right {
  gap: 0.5rem;
  justify-self: end;
}

.app-header-center {
  display: flex;
  align-items: center;
  justify-self: center;
  min-width: 0;
}
</style>

<style>
/* Unscoped: shared "look" for controls each view places into AppHeader's
   own slots — slot content renders in the parent's own scope, which a
   scoped selector here could never reach.
   .app-header-icon-btn: the small square icon button every view's back
   arrow uses. ProjectsMenu.vue's own .projects-btn already matches this
   look pixel-for-pixel (same size/border/color), so the overlay-only
   rules below reach it too rather than duplicating them there.
   .app-header-title: the header's own h2, for a view that needs one. */
.app-header-icon-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  padding: 0;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.app-header-icon-btn:hover {
  background: #4a6fa5;
  color: white;
}

/* Live chat's overlay controls sit on top of a project's own skin — kept
   unobtrusive until touched, same idiom as the old floating Settings/
   Profile cluster this replaced. */
.app-header-overlay .app-header-icon-btn,
.app-header-overlay .projects-btn {
  opacity: 0.35;
  transition: opacity 0.15s ease;
}

.app-header-overlay .app-header-icon-btn:hover,
.app-header-overlay .projects-btn:hover {
  opacity: 1;
}

@media (hover: none) and (pointer: coarse) {
  .app-header-overlay .app-header-icon-btn,
  .app-header-overlay .projects-btn {
    width: 2.75rem;
    height: 2.75rem;
    opacity: 1;
  }
}

.app-header-title {
  margin: 0;
  font-size: 1.1rem;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
