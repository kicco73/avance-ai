import { ref } from 'vue'
import { AUTO_DISMISS_MS } from '../../toastStore.js'

export const chatNotifications = ref([])

let nextId = 0

export function dismissChatNotification(id) {
  chatNotifications.value = chatNotifications.value.filter((notification) => notification.id !== id)
}

export function notifyInChat(title, body) {
  const id = ++nextId
  chatNotifications.value = [...chatNotifications.value, { id, title, body }]
  setTimeout(() => dismissChatNotification(id), AUTO_DISMISS_MS)
}
