import { ref } from 'vue'

// A small hover/focus tooltip immune to being clipped by a narrow,
// `overflow: hidden` ancestor — renders via <Teleport to="body"> with
// `position: fixed`. For a v-for list, pass the row's element to show(el).
export function useFloatingTooltip() {
  const triggerRef = ref(null)
  const visible = ref(false)
  const style = ref({})

  function show(target) {
    // A bare method reference like `@mouseenter="show"` passes the DOM
    // Event, not an element, so a non-Element target falls back to triggerRef.
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
