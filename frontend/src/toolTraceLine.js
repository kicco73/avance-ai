// The permanent line left under an assistant message for one tool call —
// composed only from the record's own fields (label, arguments, rows,
// error), matching exactly what a live turn's own message.toolCalls
// entry and a reloaded session's persisted record both carry (see
// chatStoreFactory.js's toStoreMessage and the submitMessage completion
// branch that fetches the persisted trace). There is no summary_text
// field anymore — this is the one place that composes the display line,
// for both the live and the reloaded path.
export function toolTraceLine(record) {
  const label = record.label || record.name
  const fields = record.arguments && typeof record.arguments.fields === 'object' ? record.arguments.fields : null
  const isUpdate = fields !== null
  const verb = isUpdate ? 'Updated' : 'Searched'
  const argumentParts = isUpdate
    ? Object.entries(fields).map(([key, value]) => `${key}="${value}"`)
    : (record.arguments?.values || []).map((value) => `"${value}"`)
  const forClause = argumentParts.length ? ` for ${argumentParts.join(', ')}` : ''
  const rows = record.rows ?? 0
  const outcome = record.error ? 'failed' : `${rows} ${rows === 1 ? 'row' : 'rows'}`
  return `${verb} ${label}${forClause} · ${outcome}`
}
