import { ref } from 'vue'

export function useSessionDragAndDrop(canDragAndDrop, selection, emit) {
  const draggingFromUsername = ref(null)
  const dragOverCounts = ref(new Map())

  function isDragOver(username) {
    return (dragOverCounts.value.get(username) || 0) > 0
  }

  function incDragOver(username) {
    const next = new Map(dragOverCounts.value)
    next.set(username, (next.get(username) || 0) + 1)
    dragOverCounts.value = next
  }

  function decDragOver(username) {
    const next = new Map(dragOverCounts.value)
    const count = (next.get(username) || 0) - 1
    if (count <= 0) next.delete(username)
    else next.set(username, count)
    dragOverCounts.value = next
  }

  function resetDragState() {
    draggingFromUsername.value = null
    dragOverCounts.value = new Map()
  }

  function isValidDropTarget(username) {
    return canDragAndDrop.value && username !== draggingFromUsername.value
  }

  function onSessionDragStart(session, user, event) {
    draggingFromUsername.value = user.username
    const selected = selection.selectedSessionIds.value
    const ids = selected.has(session.id) ? [...selected] : [session.id]
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('application/json', JSON.stringify({ sessionIds: ids, fromUsername: user.username }))
  }

  function onBranchDragEnter(user, event) {
    if (!isValidDropTarget(user.username)) return
    event.preventDefault()
    incDragOver(user.username)
  }

  function onBranchDragLeave(user) {
    if (!isValidDropTarget(user.username)) return
    decDragOver(user.username)
  }

  function onBranchDragOver(user, event) {
    if (!isValidDropTarget(user.username)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }

  function onBranchDrop(user, event) {
    if (!isValidDropTarget(user.username)) return
    event.preventDefault()
    const raw = event.dataTransfer.getData('application/json')
    resetDragState()
    if (!raw) return
    let payload
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }
    if (!payload?.sessionIds?.length || payload.fromUsername === user.username) return
    emit('move-sessions', { sessionIds: payload.sessionIds, username: user.username })
    selection.clearSelection()
  }

  return {
    isDragOver, onSessionDragStart, onSessionDragEnd: resetDragState,
    onBranchDragEnter, onBranchDragLeave, onBranchDragOver, onBranchDrop,
  }
}
