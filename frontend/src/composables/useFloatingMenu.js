import { nextTick, onBeforeUnmount, ref } from 'vue'

// A menu panel anchored to its own trigger button, positioned via
// getBoundingClientRect so it can escape an `overflow: hidden` ancestor
// (the click-to-toggle sibling of useFloatingTooltip.js's hover variant) —
// closed by an outside click, a page scroll, or a viewport resize.
export function useFloatingMenu() {
  const open = ref(false)
  const triggerRef = ref(null)
  const panelRef = ref(null)
  const style = ref({})

  function position() {
    const trigger = triggerRef.value
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    style.value = { left: `${rect.left}px`, top: `${rect.bottom + 4}px` }
  }

  function close() {
    open.value = false
  }

  async function toggle() {
    open.value = !open.value
    if (open.value) await nextTick().then(position)
  }

  function handleClickOutside(event) {
    if (!open.value) return
    if (triggerRef.value?.contains(event.target)) return
    if (panelRef.value?.contains(event.target)) return
    close()
  }

  document.addEventListener('click', handleClickOutside, true)
  window.addEventListener('resize', close)
  window.addEventListener('scroll', close, true)
  onBeforeUnmount(() => {
    document.removeEventListener('click', handleClickOutside, true)
    window.removeEventListener('resize', close)
    window.removeEventListener('scroll', close, true)
  })

  return { open, triggerRef, panelRef, style, toggle, close }
}
