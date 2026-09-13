import { ref } from 'vue'

export const audioEnabled = ref(false)
export const spokenTextEnabled = ref(false)

export function toggleSpokenText() {
  spokenTextEnabled.value = !spokenTextEnabled.value
}
