import { ref } from 'vue'

import { onServices } from '../../skillServices.js'

export const configured = ref(true)

export const stateListener = {
  // The server's own switch, read once at boot: whether this build has a
  // provider at all. It says nothing about the conversation on screen.
  stateReceived(state) {
    configured.value = state.talk_enabled ?? true
  }
}

// What the conversation on screen can actually reach — its own project's
// answer, said when it opens (see backend docs/BUS.md's own
// session.info). This is the one that decides; the boot flag above only
// says whether this build has a provider at all.
onServices((available) => {
  configured.value = available.talk ?? true
})
