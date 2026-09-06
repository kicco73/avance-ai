import { computed, ref, watch } from 'vue'
import { getMessages, getProjectSignals, getSessionSignals, getUsers } from '../api.js'
import { sessions } from '../chatStore.js'

// Test mode's own selection ('root' | 'sessions-branch' | 'states-branch' |
// `session:<id>` | `state:<key>` | `user:<name>` | `signal:<name>` | null),
// resolved against the project's session catalog, users and signals for the
// Inspector's read-only Info/User tabs.
export function useTestModeSelection(projectId, indexYmlEditorRef) {
  const autoSelectedNodeId = ref(null)
  function handleAutoSelect(nodeId) { autoSelectedNodeId.value = nodeId }

  function idAfter(prefix) {
    const id = autoSelectedNodeId.value
    return id && id.startsWith(prefix) ? id.slice(prefix.length) : null
  }

  const autoSelectedSessionId = computed(() => {
    const raw = idAfter('session:')
    return raw == null ? null : Number(raw)
  })
  const autoSelectedSession = computed(() => {
    const id = autoSelectedSessionId.value
    return id == null ? null : (sessions.value.find((s) => s.id === id) ?? null)
  })
  const autoSelectedStateKey = computed(() => idAfter('state:'))

  const usersList = ref([])
  let usersListLoaded = false
  async function ensureUsersList() {
    if (usersListLoaded) return
    usersListLoaded = true
    try {
      usersList.value = (await getUsers()).users
    } catch {
      // already surfaced via apiFetch
    }
  }
  const autoSelectedUsername = computed(() => idAfter('user:') ?? autoSelectedSession.value?.username ?? null)
  const autoSelectedUser = computed(() => {
    const username = autoSelectedUsername.value
    return username == null ? null : (usersList.value.find((u) => u.email === username || u.id === username) ?? null)
  })

  function stateElementFor(key) {
    return key == null ? null : (indexYmlEditorRef.value?.stateElementFor(key) ?? null)
  }
  const autoSelectedElement = computed(() => stateElementFor(autoSelectedStateKey.value))

  const signalsList = ref([])
  let signalsListLoaded = false
  async function ensureSignalsList() {
    if (signalsListLoaded) return
    signalsListLoaded = true
    try {
      signalsList.value = (await getProjectSignals(projectId, null, null)).signals
    } catch {
      // already surfaced via apiFetch
    }
  }
  const autoSelectedSignalName = computed(() => idAfter('signal:'))
  const autoSelectedSignal = computed(() => {
    const name = autoSelectedSignalName.value
    return name == null ? null : (signalsList.value.find((s) => s.signal.name === name)?.signal ?? null)
  })

  const autoSessionSignals = ref([])
  const autoSessionMessages = ref([])
  watch(autoSelectedSessionId, async (id) => {
    autoSessionSignals.value = id == null ? [] : await getSessionSignals(id).catch(() => [])
  })
  watch(autoSelectedSessionId, async (id) => {
    autoSessionMessages.value = id == null ? [] : await getMessages(id).catch(() => [])
  })
  const autoSessionInputTokens = computed(() => {
    const userMessages = autoSessionMessages.value.filter((m) => m.role === 'user')
    if (!userMessages.some((m) => m.tokens != null)) return null
    return userMessages.reduce((sum, m) => sum + (m.tokens ?? 0), 0)
  })

  // An imported session never ran against the automaton: its first/last
  // annotated expected_state stands in for start_state/end_state.
  const autoSessionIsImported = computed(() => autoSelectedSession.value?.type === 'imported')
  const autoSessionAnnotatedStates = computed(() => autoSessionSignals.value.map((row) => row.expected_state).filter(Boolean))
  const autoSessionStartStateKey = computed(() => (
    autoSessionIsImported.value ? (autoSessionAnnotatedStates.value[0] ?? null) : (autoSelectedSession.value?.start_state ?? null)
  ))
  const autoSessionEndStateKey = computed(() => (
    autoSessionIsImported.value ? (autoSessionAnnotatedStates.value.at(-1) ?? null) : (autoSelectedSession.value?.end_state ?? null)
  ))
  const autoSessionStartElement = computed(() => stateElementFor(autoSessionStartStateKey.value))
  const autoSessionEndElement = computed(() => stateElementFor(autoSessionEndStateKey.value))

  return {
    autoSelectedNodeId, handleAutoSelect, autoSelectedSession, autoSelectedStateKey, autoSelectedElement,
    autoSelectedUser, autoSelectedSignalName, autoSelectedSignal, autoSessionInputTokens,
    autoSessionStartElement, autoSessionEndElement, ensureUsersList, ensureSignalsList,
  }
}
