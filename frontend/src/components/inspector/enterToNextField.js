// Enter, in a single-line <input> inside one of the Inspector's own edit
// cards (a state/action/signal title — see InspectorDetailCard.vue/
// InspectorSignalsTab.vue), commits it (blur already does that — see each
// field's own @blur) and moves focus to the next such input in the same
// card, cycling back to the first once past the last. Deliberately
// scoped to <input> only, never <textarea> — a textarea's own Enter
// still just types a newline, which every multi-line field here
// (description/prompt/definition) genuinely needs.
//
// Scoped to the whole *card* (.inspector-detail-card/.inspector-signal-
// block), not just its inner .inspector-detail-form/.inspector-signal-
// form — a title input (the one every card actually has today) lives in
// the card's own header, a sibling of that inner form div, never a
// descendant of it. Scoping to the form alone left the title input's own
// closest() come up empty, silently swallowing its Enter (prevented by
// the caller's own .prevent, but this function then did nothing at all)
// instead of committing it.
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
