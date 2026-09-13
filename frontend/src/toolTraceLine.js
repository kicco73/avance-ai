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
