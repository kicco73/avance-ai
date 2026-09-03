// The one consumer of chatClient's single notification handler.
//
// Every server-pushed "notification" frame carries at most an on-enter
// script (an action's actuator.celebrate()/notify()/show() output — which
// the backend now always delivers this way, from the OnEnterTask that ran
// it, never inside a turn's own response) and/or a state update for a
// project (a cross-project wake-up). The script is a global UI effect —
// a toast, confetti, a dialog — so it runs exactly once here, whichever
// chat stores happen to exist; only the state part is fanned out, to
// every store that asked, each deciding whether it is about its project.
import { onNotification } from './chatClient.js'
import { runOnEnterScript } from './onEnterActions.js'

const stateSubscribers = new Set()
let registered = false

function ensureRegistered() {
  if (registered) return
  registered = true
  onNotification(({ project_name, state, 'on-enter': onEnter }) => {
    if (onEnter) runOnEnterScript(onEnter)
    if (state) {
      for (const subscriber of stateSubscribers) subscriber({ project_name, state })
    }
  })
}

// `handler({ project_name, state })` for every state-carrying frame.
// Returns an unsubscribe function.
export function subscribeToStateNotifications(handler) {
  ensureRegistered()
  stateSubscribers.add(handler)
  return () => stateSubscribers.delete(handler)
}

// Test seam: what ensureRegistered handed chatClient, so a test can feed
// frames in without a socket.
export function _resetNotificationBusForTests() {
  stateSubscribers.clear()
  registered = false
}
