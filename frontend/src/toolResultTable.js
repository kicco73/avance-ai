import Papa from 'papaparse'

function escapeCell(value) {
  return String(value ?? '').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ')
}

export function csvToMarkdownTable(csv) {
  const text = (csv ?? '').trim()
  if (!text || text.startsWith('error:')) return ''
  const parsed = Papa.parse(text, { skipEmptyLines: true })
  const rows = (parsed.data ?? []).filter((row) => row.some((cell) => String(cell ?? '').trim() !== ''))
  if (rows.length < 2 || rows[0].length < 2) return ''
  const width = Math.max(...rows.map((row) => row.length))
  const squared = rows.map((row) => [...row, ...Array(width - row.length).fill('')].map(escapeCell))
  const [header, ...body] = squared
  return [
    `| ${header.join(' | ')} |`,
    `| ${header.map(() => '---').join(' | ')} |`,
    ...body.map((row) => `| ${row.join(' | ')} |`)
  ].join('\n')
}
