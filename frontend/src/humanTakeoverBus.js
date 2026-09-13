import { busChannel } from './busChannel.js'
import { isWatched } from './watchedSessions.js'
import { addHumanTakeover } from './humanTakeoverStore.js'

busChannel.subscribe('session.taken_over', (frame) => {
  if (isWatched(frame.session_id)) return
  addHumanTakeover(frame.session_id, frame.project_id)
})
