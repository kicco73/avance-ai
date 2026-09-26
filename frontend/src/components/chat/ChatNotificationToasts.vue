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
      <div class="chat-notification-layout">
        <img v-if="notification.iconUrl" :src="notification.iconUrl" alt="" class="chat-notification-icon" />
        <div class="chat-notification-text">
          <div class="chat-notification-title">{{ notification.title }}</div>
          <div class="chat-notification-body" v-html="renderMarkdown(notification.body)"></div>
        </div>
      </div>
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

.chat-notification-layout {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}

.chat-notification-icon {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 0.6rem;
  object-fit: cover;
}

.chat-notification-text {
  flex: 1;
  min-width: 0;
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
