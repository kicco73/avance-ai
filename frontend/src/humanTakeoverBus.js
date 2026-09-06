// The frontend half of actuator.switch_to_human(user_id) (see chat/
// ws_notifications.py's send_human_takeover): a human_takeover frame
// means some session needs a person now. If this very tab is already
// looking at that session there's nothing to do — otherwise it's queued
// for HumanTakeoverToasts.vue to show as a link.
//
// Registers itself as soon as this module is imported (mirrors
// humanPromptBus.js's own module-level subscribe pattern) —
// HumanTakeoverToasts.vue imports it purely for this side effect.
import { chatChannel } from './chatChannel.js'
import { currentSessionId } from './chatStore.js'
import { addHumanTakeover } from './humanTakeoverStore.js'

chatChannel.subscribe('human_takeover', (frame) => {
  if (currentSessionId.value === frame.session_id) return
  addHumanTakeover(frame.session_id, frame.project_id)
})
