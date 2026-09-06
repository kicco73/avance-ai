<script setup>
// Opened from a human_takeover notification's "Open" link (see
// humanTakeoverStore.js, App.vue) — actuator.switch_to_human(user_id)
// handed this session to whoever is looking at this. Deliberately its own
// component rather than a branch inside ChatView.vue: it reuses ChatView
// wholesale through the two extension points it already has (a generic
// `store` prop, and the `timeline` slot) instead of an if/else that would
// have to thread an "operator" mode through its whole render.
import ChatView from './ChatView.vue'
import MessageBubble from './MessageBubble.vue'
import { createOperatorChatStore } from '../../chatStoreFactory.js'

const props = defineProps({
  sessionId: { type: [Number, String], required: true },
  projectId: { type: [String, null], default: null },
  profile: { type: Object, default: null }
})

defineEmits(['home', 'profile', 'logout', 'close'])

// One store per (sessionId, projectId) pair, built once for this
// component's lifetime — App.vue keys its instance by sessionId (same
// :key="editProjectId" pattern EditProjectView.vue's own pushed view
// already uses), so a different session always remounts rather than
// reusing this store for the wrong one.
const operatorStore = createOperatorChatStore(props.sessionId, props.projectId)
</script>

<template>
  <ChatView
    :store="operatorStore"
    hide-sessions-panel
    role="admin"
    :profile="profile"
    @home="$emit('home')"
    @profile="$emit('profile')"
    @logout="$emit('logout')"
    @manage-projects="$emit('close')"
  >
    <template #timeline>
      <MessageBubble
        v-for="msg in operatorStore.messages.value"
        :key="msg.id"
        :message="msg"
        invert
        show-timestamp
      />
    </template>
  </ChatView>
</template>
