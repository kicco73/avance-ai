import { busChannel } from './busChannel.js'
import { runTaskScript } from './taskActions.js'

let registered = false

export function watchPushedTasks() {
  if (registered) return
  registered = true
  busChannel.subscribe('ui.notification', ({ task }) => {
    if (task) runTaskScript(task)
  })
}

export function _resetNotificationBusForTests() {
  registered = false
}
