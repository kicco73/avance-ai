// Enter, in a single-line <input> inside one of the Inspector's own edit
// forms (a state/action/signal title — see InspectorDetailCard.vue/
// InspectorSignalsTab.vue), commits it (blur already does that — see each
// field's own @blur) and moves focus to the next such input in the same
// form, cycling back to the first once past the last. Deliberately
// scoped to <input> only, never <textarea> — a textarea's own Enter
// still just types a newline, which every multi-line field here
// (description/prompt/definition) genuinely needs.
export function handleEnterNext(event) {
  const form = event.target.closest('.inspector-detail-form, .inspector-signal-form')
  if (!form) return
  const inputs = Array.from(form.querySelectorAll('input:not([type=checkbox])'))
  const index = inputs.indexOf(event.target)
  event.target.blur()
  const next = inputs[index + 1] ?? inputs[0]
  next?.focus()
  next?.select?.()
}
