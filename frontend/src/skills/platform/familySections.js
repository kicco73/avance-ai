const UNGROUPED_KEY = '__ungrouped__'
const UNGROUPED_LABEL = 'Other'

function byTitle(entries) {
  return [...entries].sort((a, b) => a.title.localeCompare(b.title))
}

export function familySections(entries) {
  const grouped = new Map()
  for (const entry of entries) {
    const key = entry.family || UNGROUPED_KEY
    grouped.set(key, [...(grouped.get(key) ?? []), entry])
  }
  const named = [...grouped.keys()]
    .filter((key) => key !== UNGROUPED_KEY)
    .sort((a, b) => a.localeCompare(b))
    .map((key) => ({ key, label: key, items: byTitle(grouped.get(key)) }))
  const ungrouped = {
    key: UNGROUPED_KEY,
    label: UNGROUPED_LABEL,
    items: byTitle(grouped.get(UNGROUPED_KEY) ?? []),
  }
  return [...named, ungrouped].filter((section) => section.items.length)
}
