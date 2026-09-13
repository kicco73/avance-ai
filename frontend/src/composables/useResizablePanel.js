import { onBeforeUnmount, ref } from 'vue'

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
