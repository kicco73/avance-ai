import { ref, watch, onMounted } from 'vue'
import { getProjectSignals } from '../../api.js'

export function useProjectSignals(props) {
  const signalsLoading = ref(true)
  const signals = ref([])

  async function loadSignals() {
    signalsLoading.value = true
    try {
      signals.value = (await getProjectSignals(props.projectId, props.stateKey, props.sessionId)).signals
    } catch {} finally { signalsLoading.value = false }
  }

  async function refresh() {
    await loadSignals()
  }

  function removeSignal(signalName) {
    signals.value = signals.value.filter((s) => s.signal.name !== signalName)
  }

  watch(() => props.stateKey, loadSignals)
  watch(() => props.sessionId, loadSignals)
  onMounted(loadSignals)

  return { signals, signalsLoading, loadSignals, refresh, removeSignal }
}
