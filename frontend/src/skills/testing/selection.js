import { computed, ref, watch } from 'vue'
import { getHistory, getProjectSignals, getSessionSignals, getUsers } from '../../api.js'
import { sessions } from '../../chatStore.js'

function createSelection() {
  let projectId = null
  let workspace = null

  function open(nextProjectId, nextWorkspace) {
    projectId = nextProjectId
    workspace = nextWorkspace
    autoSelectedNodeId.value = null
    usersListLoaded = false
    signalsListLoaded = false
    ensureUsersList()
    ensureSignalsList()
  }
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
    }
  }
  const autoSelectedUsername = computed(() => idAfter('user:') ?? autoSelectedSession.value?.username ?? null)
  const autoSelectedUser = computed(() => {
    const username = autoSelectedUsername.value
    return username == null ? null : (usersList.value.find((u) => u.email === username || u.id === username) ?? null)
  })

  function stateElementFor(key) {
    return key == null ? null : (workspace?.stateElementFor(key) ?? null)
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
    autoSessionMessages.value = id == null ? [] : await getHistory(id).catch(() => [])
  })
  const autoSessionInputTokens = computed(() => {
    const userMessages = autoSessionMessages.value.filter((m) => m.role === 'user')
    if (!userMessages.some((m) => m.tokens != null)) return null
    return userMessages.reduce((sum, m) => sum + (m.tokens ?? 0), 0)
  })

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
    open, handleAutoSelect,
    nodeId: autoSelectedNodeId,
    session: autoSelectedSession,
    stateKey: autoSelectedStateKey,
    element: autoSelectedElement,
    user: autoSelectedUser,
    signalName: autoSelectedSignalName,
    signal: autoSelectedSignal,
    sessionInputTokens: autoSessionInputTokens,
    sessionStartElement: autoSessionStartElement,
    sessionEndElement: autoSessionEndElement,
  }
}

export const selection = createSelection()
