import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

// Whether the centered header logo still fits between the left and right
// header groups; `headerEl` is the AppHeader component ref (its `.el`).
export function useHeaderLogoFit(headerEl, headerLeftEl, headerActionsEl) {
  const headerWidth = ref(0)
  const headerLeftWidth = ref(0)
  const headerActionsWidth = ref(0)
  const logoWidth = ref(0)
  let resizeObserver = null

  const logoFits = computed(
    () => !logoWidth.value || headerWidth.value - headerLeftWidth.value - headerActionsWidth.value >= logoWidth.value
  )

  function setLogoBtnEl(el) {
    if (!el || logoWidth.value > 0) return
    nextTick(() => { logoWidth.value = el.getBoundingClientRect().width })
  }

  function handleResize(entries) {
    for (const entry of entries) {
      const width = entry.contentRect.width
      if (entry.target === headerEl.value.el) headerWidth.value = width
      else if (entry.target === headerLeftEl.value) headerLeftWidth.value = width
      else if (entry.target === headerActionsEl.value) headerActionsWidth.value = width
    }
  }

  onMounted(() => {
    resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(headerEl.value.el)
    resizeObserver.observe(headerLeftEl.value)
    resizeObserver.observe(headerActionsEl.value)
  })

  onBeforeUnmount(() => resizeObserver?.disconnect())

  return { logoFits, setLogoBtnEl }
}
