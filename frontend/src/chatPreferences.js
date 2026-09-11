import { ref } from 'vue'

// App-wide user preferences about a conversation, kept away from the chat
// store itself so that whoever contributes a control for one of them can
// read it without importing the store that assembles those controls.
export const audioEnabled = ref(false)
export const spokenTextEnabled = ref(false)

export function toggleSpokenText() {
  spokenTextEnabled.value = !spokenTextEnabled.value
}
