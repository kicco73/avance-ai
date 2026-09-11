// The frontend half of chat.switch_to_human(user_id) (see chat/
// bus_channel.py's send_human_takeover): a human_takeover frame
// means some session needs a person now. If this very tab is already
// looking at that session there's nothing to do — otherwise it's queued
// for HumanTakeoverToasts.vue to show as a link.
//
// Registers itself as soon as this module is imported (mirrors
// humanPromptBus.js's own module-level subscribe pattern) —
// HumanTakeoverToasts.vue imports it purely for this side effect.
import { busChannel } from './busChannel.js'
import { isWatched } from './watchedSessions.js'
import { addHumanTakeover } from './humanTakeoverStore.js'

busChannel.subscribe('ui.human_takeover', (frame) => {
  // Already looking at this exact session — nothing to alert this tab
  // about; the other case a self-targeted chat.switch_to_human(user.email)
  // hits while testing your own bot from a single tab. A screen with a
  // chat of its own says which session it is showing (see
  // watchedSessions.js), so the core never has to name one.
  if (isWatched(frame.session_id)) return
  addHumanTakeover(frame.session_id, frame.project_id)
})
