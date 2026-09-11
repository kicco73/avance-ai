<script setup>
// The one "the session is still starting up" panel, for every kind of
// chat there is — the app-store preview, EditProjectView's Test chat and
// the live chat all wait on the same thing (a session being created and
// its history loading) and now all show the same thing while they do.
// It began as the app-store preview's own spinner and stayed there alone
// for a while; the other two showed nothing at all.
//
// An overlay rather than a replacement screen: whatever the chat already
// has on it — the frozen sample transcript, the previous session, an
// empty shell — stays visible underneath, veiled and unclickable, so the
// panel appearing and disappearing doesn't reflow the window.
//
// Its parent must be a positioned box. It sits under an AppHeader's own
// overlay controls (z-index 20 there, see ChatView.vue) for the same
// reason ChatSupersededOverlay does.
import ProgressSpinner from '../ProgressSpinner.vue'
</script>

<template>
  <div class="chat-waiting-panel">
    <ProgressSpinner />
  </div>
</template>

<style scoped>
.chat-waiting-panel {
  position: absolute;
  inset: 0;
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #4a6fa5;
  /* Washes out what's underneath the same way the app store's own frozen
     preview used to wash out its sample transcript (a brightness/
     saturation filter on the content itself) — as a veil instead, so the
     content below needs no styling of its own to participate. */
  background: rgba(255, 255, 255, 0.62);
  pointer-events: all;
}

.chat-waiting-panel svg {
  width: 32px;
  height: 32px;
}
</style>
