import { onBeforeUnmount, ref } from 'vue'

// Drag-to-resize a split panel divider. `invert: true` for a divider that
// sits on its own panel's left edge, where dragging left (negative
// movementX) must grow the panel rather than shrink it. `onResize`, if
// given, runs after every width change (e.g. to nudge a Cytoscape graph
// that doesn't notice its container resized on its own).
export function useResizablePanel(initialWidth, { min, max, invert = false, onResize } = {}) {
  const width = ref(initialWidth)

  function onDrag(event) {
    const delta = invert ? -event.movementX : event.movementX
    width.value = Math.min(max, Math.max(min, width.value + delta))
    onResize?.()
  }

  function stopDrag() {
    window.removeEventListener('mousemove', onDrag)
    window.removeEventListener('mouseup', stopDrag)
  }

  function startDrag(event) {
    event.preventDefault()
    window.addEventListener('mousemove', onDrag)
    window.addEventListener('mouseup', stopDrag)
  }

  onBeforeUnmount(stopDrag)

  return { width, startDrag }
}
