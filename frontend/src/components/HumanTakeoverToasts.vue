<script setup>
// Surface for actuator.switch_to_human(user_id) (see chat/ws_notifications.py,
// humanTakeoverBus.js) — mounted once at the app root alongside
// HumanPromptToasts.vue, top-left so the two never overlap. Clicking
// "Open" hands the target session off to App.vue (via
// requestedOperatorSession) rather than navigating here directly — only
// App.vue holds the view-stack/pushView machinery.
import { humanTakeovers, openHumanTakeover, dismissHumanTakeover } from '../humanTakeoverStore.js'
import '../humanTakeoverBus.js'
</script>

<template>
  <div class="human-takeover-container">
    <TransitionGroup name="human-takeover">
      <div v-for="takeover in humanTakeovers" :key="takeover.id" class="human-takeover-card">
        <span class="human-takeover-text">Session {{ takeover.sessionId }} needs a human</span>
        <button
          type="button"
          class="human-takeover-open"
          @click="openHumanTakeover(takeover.id, takeover.sessionId, takeover.projectId)"
        >Open</button>
        <button type="button" class="human-takeover-close" title="Dismiss" @click="dismissHumanTakeover(takeover.id)">×</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.human-takeover-container {
  position: fixed;
  top: 1rem;
  left: 1rem;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  width: 280px;
  max-width: calc(100vw - 2rem);
  pointer-events: none;
}

.human-takeover-card {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  background: #2b6cb0;
  color: white;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
}

.human-takeover-text {
  flex: 1;
  font-size: 0.82rem;
  line-height: 1.3;
}

.human-takeover-open {
  flex-shrink: 0;
  border: none;
  border-radius: 6px;
  background: white;
  color: #2b6cb0;
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0.3rem 0.6rem;
  cursor: pointer;
}

.human-takeover-close {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: white;
  cursor: pointer;
  font-size: 1rem;
  opacity: 0.85;
}

.human-takeover-close:hover {
  opacity: 1;
}

.human-takeover-enter-active, .human-takeover-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.human-takeover-enter-from {
  opacity: 0;
  transform: translateX(-20px);
}

.human-takeover-leave-to {
  opacity: 0;
}
</style>
