import { ref } from 'vue'

export function useFloatingTooltip() {
  const triggerRef = ref(null)
  const visible = ref(false)
  const style = ref({})

  function show(target) {
    const el = target instanceof Element ? target : triggerRef.value
    if (!el) return
    const rect = el.getBoundingClientRect()
    style.value = {
      bottom: `${window.innerHeight - rect.top + 6}px`,
      right: `${window.innerWidth - rect.right}px`
    }
    visible.value = true
  }

  function hide() {
    visible.value = false
  }

  return { triggerRef, visible, style, show, hide }
}
