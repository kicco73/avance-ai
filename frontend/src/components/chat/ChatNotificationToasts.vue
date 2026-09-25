<script setup>
import ChatToastCard from './ChatToastCard.vue'
import { chatNotifications, dismissChatNotification } from './chatNotifications.js'
import { renderMarkdown } from '../../markdown.js'
</script>

<template>
  <TransitionGroup name="chat-toast" tag="div" class="chat-notifications">
    <ChatToastCard
      v-for="notification in chatNotifications"
      :key="notification.id"
      class="chat-notification"
      role="status"
      title="Dismiss"
      @click="dismissChatNotification(notification.id)"
    >
      <div class="chat-notification-title">{{ notification.title }}</div>
      <div class="chat-notification-body" v-html="renderMarkdown(notification.body)"></div>
    </ChatToastCard>
  </TransitionGroup>
</template>

<style scoped>
.chat-notifications {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.6rem;
}

.chat-notification {
  pointer-events: auto;
  cursor: pointer;
}

.chat-notification-title {
  font-size: 0.9rem;
  font-weight: 600;
  letter-spacing: 0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-notification-body {
  margin-top: 0.4rem;
  font-size: 0.85rem;
  line-height: 1.4;
  opacity: 0.85;
  overflow-wrap: anywhere;
}

.chat-notification-body :deep(p) {
  margin: 0;
}

.chat-notification-body :deep(a) {
  color: #9ecbff;
}
</style>
