import { ref } from 'vue'

export const chatNotifications = ref([])

const AUTO_DISMISS_MS = 8000

let nextId = 0

export function dismissChatNotification(id) {
  chatNotifications.value = chatNotifications.value.filter((notification) => notification.id !== id)
}

export function notifyInChat(title, body, iconUrl = null) {
  const id = ++nextId
  chatNotifications.value = [...chatNotifications.value, { id, title, body, iconUrl }]
  setTimeout(() => dismissChatNotification(id), AUTO_DISMISS_MS)
}
