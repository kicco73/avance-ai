// A <textarea v-autosize> grows to fit its own content exactly, up to a
// 10-line cap (an internal scrollbar takes over past that) — unlike a
// static `rows` count based on the text's own '\n' characters (the
// previous approach here), which undercounts a long unwrapped paragraph
// that only *visually* wraps into several lines, leaving it cramped.
// Never auto-shrinks below its current height, so it doesn't fight a
// manual drag-resize (the textarea keeps `resize: vertical`) or an
// in-progress edit that temporarily gets shorter.
const MAX_LINES = 10

function maxHeightFor(el, style) {
  const lineHeight = parseFloat(style.lineHeight) || 18
  const paddingY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) || 0
  const borderY = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth) || 0
  return Math.round(lineHeight * MAX_LINES + paddingY + borderY)
}

function resize(el) {
  if (!el) return
  const style = window.getComputedStyle(el)
  const currentHeight = el.style.height ? parseFloat(el.style.height) : 0
  el.style.height = 'auto'
  const contentHeight = el.scrollHeight
  const maxHeight = maxHeightFor(el, style)
  const finalHeight = Math.max(Math.min(contentHeight, maxHeight), currentHeight)
  el.style.height = finalHeight + 'px'
  el.style.overflowY = contentHeight > finalHeight ? 'auto' : 'hidden'
}

export const vAutosize = {
  mounted(el) {
    resize(el)
    el.addEventListener('input', () => resize(el))
  },
  updated(el) {
    resize(el)
  }
}
