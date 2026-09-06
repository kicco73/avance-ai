import { ref } from 'vue'

export function useSessionSelection(canDragAndDrop, emit) {
  const selectedSessionIds = ref(new Set())
  const selectionAnchor = ref(null)

  function clearSelection() {
    selectedSessionIds.value = new Set()
  }

  function onSessionRowClick(session, user, event) {
    if (!canDragAndDrop.value) {
      emit('select', `session:${session.id}`)
      return
    }
    if (event.shiftKey && selectionAnchor.value != null) {
      const ids = user.sessions.map((s) => s.id)
      const anchorIndex = ids.indexOf(selectionAnchor.value)
      const clickedIndex = ids.indexOf(session.id)
      if (anchorIndex !== -1 && clickedIndex !== -1) {
        const [start, end] = anchorIndex < clickedIndex ? [anchorIndex, clickedIndex] : [clickedIndex, anchorIndex]
        selectedSessionIds.value = new Set(ids.slice(start, end + 1))
      }
      return
    }
    if (event.ctrlKey || event.metaKey) {
      const next = new Set(selectedSessionIds.value)
      if (next.has(session.id)) next.delete(session.id)
      else next.add(session.id)
      selectedSessionIds.value = next
      selectionAnchor.value = session.id
      return
    }
    selectedSessionIds.value = new Set([session.id])
    selectionAnchor.value = session.id
    emit('select', `session:${session.id}`)
  }

  return { selectedSessionIds, clearSelection, onSessionRowClick }
}
