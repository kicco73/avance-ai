import { ref } from 'vue'

// A small hover/focus tooltip that's immune to being clipped by a narrow,
// `overflow: hidden` ancestor (e.g. Inspector.vue's own split-view panel)
// — unlike a normal `position: absolute` tooltip nested inside the
// trigger, or (per user report) the browser's own native `title`
// attribute, which wasn't rendering reliably at all in this environment.
// Works by computing the trigger's own viewport position on
// show() and rendering the tooltip via <Teleport to="body"> with
// `position: fixed`, so it's never subject to any ancestor's overflow.
//
// Usage (single, fixed trigger):
//   const { triggerRef, visible, style, show, hide } = useFloatingTooltip()
//   <span ref="triggerRef" @mouseenter="show" @mouseleave="hide" @focus="show" @blur="hide">?</span>
//   <Teleport to="body">
//     <span v-if="visible" class="my-tooltip" :style="style">...</span>
//   </Teleport>
//
// Usage (one shared tooltip across a v-for list — a template ref can't
// target a specific iteration, so pass the row's own element instead):
//   <span @mouseenter="show($event.currentTarget)" @mouseleave="hide" ...>!</span>
export function useFloatingTooltip() {
  const triggerRef = ref(null)
  const visible = ref(false)
  const style = ref({})

  function show(target) {
    // Vue passes the native DOM Event as the sole argument to a bare
    // method reference like `@mouseenter="show"` — not an element — so a
    // non-Element target (the common case for the single-trigger usage)
    // must fall back to triggerRef.value rather than being used as-is.
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
