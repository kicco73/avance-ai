import { ref } from 'vue'

import { onServices } from '../../skillServices.js'

export const configured = ref(true)

export const stateListener = {
  stateReceived(state) {
    configured.value = state.listen_enabled ?? true
  }
}

onServices((available) => {
  configured.value = available.listen ?? true
})
