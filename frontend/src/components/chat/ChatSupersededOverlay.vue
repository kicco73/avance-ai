<script setup>
// What the chat looks like once another client of the same identity took
// the channel over (see chatChannel.js's 'superseded' connectionState).
// The newest connection wins on the backend, so this window is done: it
// can neither send nor receive, and everything it still shows is a
// snapshot of a conversation someone else is now holding.
//
// It veils and blocks the chat area only. The window's own AppHeader is
// absolutely positioned above this (z-index 20 there against this one's
// 15), so its controls — the projects menu, the profile menu, the way
// back out of here — stay reachable and undimmed, which is the whole
// point: this state ends by navigating away, not by anything inside the
// chat itself.
</script>

<template>
  <div class="chat-superseded-overlay">
    <div class="chat-superseded-card">
      <h2 class="chat-superseded-title">Chat spostata su un altro client</h2>
      <p class="chat-superseded-body">
        Questa conversazione è stata aperta su un'altra finestra o dispositivo, che ora
        ha il controllo del canale. Da qui non è più possibile inviare né ricevere messaggi.
      </p>
    </div>
  </div>
</template>

<style scoped>
.chat-superseded-overlay {
  position: absolute;
  /* Starts below the header strip a project's skin paints — the same
     band AppHeader's own overlay controls sit in (see ChatView.vue's
     --chat-header-height). */
  top: var(--chat-header-height);
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 15;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 1rem;
  background: rgba(0, 0, 0, 0.55);
  /* The point of the overlay: nothing underneath is clickable. */
  pointer-events: all;
  overflow-y: auto;
}

/* Same card as DialogHost.vue's own .dialog-card — deliberately, so this
   reads as the app's own dialog rather than a one-off panel. It is not a
   real <dialog>: the header above must stay usable, which a modal one
   would take away. */
.chat-superseded-card {
  flex-shrink: 0;
  width: 100%;
  max-width: 420px;
  box-sizing: border-box;
  background: white;
  border: 1px solid #ddd;
  border-radius: 10px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
  padding: 1.2rem 1.4rem;
}

.chat-superseded-title {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.chat-superseded-body {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.5;
  color: #444;
}
</style>
