import { busChannel } from './busChannel.js'
import { runTaskScript } from './taskActions.js'

let registered = false

function addresses(chat, { session_id, project_id }) {
  if (session_id != null) return session_id === chat.currentSessionId.value
  return project_id === chat.currentProjectId.value
}

export function watchPushedTasks(chat) {
  if (registered) return
  registered = true
  busChannel.subscribe('ui.notification', (frame) => {
    if (frame.task && addresses(chat, frame)) runTaskScript(frame.task)
  })
}

export function _resetNotificationBusForTests() {
  registered = false
}
