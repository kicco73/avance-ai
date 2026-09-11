// Moves focus to the next <input> within the same card on Enter, cycling back to
// the first past the last. Scoped to the whole card (not just the inner form)
// since the title input lives in the card header, a sibling of the form div.
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
