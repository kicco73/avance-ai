export function handleEnterNext(event) {
  const card = event.target.closest('.inspector-detail-card, .inspector-signal-block')
  if (!card) return
  const inputs = Array.from(card.querySelectorAll('input:not([type=checkbox])'))
  const index = inputs.indexOf(event.target)
  event.target.blur()
  const next = inputs[index + 1] ?? inputs[0]
  next?.focus()
  next?.select?.()
}
