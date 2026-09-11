// A <textarea v-autosize> grows to fit its content, capped at 10 lines (a
// scrollbar takes over past that). Never auto-shrinks below its current height,
// so it doesn't fight a manual drag-resize or a mid-edit paragraph getting shorter.
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
