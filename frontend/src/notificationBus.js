import { busChannel } from './busChannel.js'
import { runTaskScript } from './taskActions.js'

const stateSubscribers = new Set()
let registered = false

function ensureRegistered() {
  if (registered) return
  registered = true
  busChannel.subscribe('ui.notification', ({ project_name, state, task }) => {
    if (task) runTaskScript(task)
    if (state) {
      for (const subscriber of stateSubscribers) subscriber({ project_name, state })
    }
  })
}

export function subscribeToStateNotifications(handler) {
  ensureRegistered()
  stateSubscribers.add(handler)
  return () => stateSubscribers.delete(handler)
}

export function _resetNotificationBusForTests() {
  stateSubscribers.clear()
  registered = false
}
