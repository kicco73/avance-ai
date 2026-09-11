import { ref } from 'vue'

export const configured = ref(true)

export const stateListener = {
  stateReceived(state) {
    configured.value = state.listen_enabled ?? true
  }
}
