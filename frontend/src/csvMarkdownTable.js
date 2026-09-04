// Renders a source's own CSV content as a Markdown table, client-side and
// live off the current (possibly unsaved) buffer — SourceContentPanel.vue's
// own Preview segment, same "renders straight off the live buffer" shape
// MdEditorPanel.vue's own Preview already has for a plain .md attachment.
// A real parser (quoted fields, embedded commas/newlines), not a naive
// comma-split — mirrors the CSV row shape Python's own csv.reader gives.

function parseCsv(text) {
  const rows = []
  let row = []
  let field = ''
  let inQuotes = false
  let i = 0
  while (i < text.length) {
    const char = text[i]
    if (inQuotes) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i += 2
        } else {
          inQuotes = false
          i += 1
        }
      } else {
        field += char
        i += 1
      }
      continue
    }
    if (char === '"') {
      inQuotes = true
      i += 1
    } else if (char === ',') {
      row.push(field)
      field = ''
      i += 1
    } else if (char === '\r') {
      i += 1
    } else if (char === '\n') {
      row.push(field)
      rows.push(row)
      row = []
      field = ''
      i += 1
    } else {
      field += char
      i += 1
    }
  }
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

function escapeCell(value) {
  return value.replace(/\|/g, '\\|').replace(/\n/g, ' ')
}

export function csvToMarkdownTable(text) {
  const rows = parseCsv(text ?? '')
  if (!rows.length) return '*(empty)*'
  const width = Math.max(...rows.map((row) => row.length))

  function renderRow(row) {
    const padded = [...row, ...Array(width - row.length).fill('')]
    return `| ${padded.map(escapeCell).join(' | ')} |`
  }

  const [header, ...dataRows] = rows
  const lines = [renderRow(header), `| ${Array(width).fill('---').join(' | ')} |`, ...dataRows.map(renderRow)]
  return lines.join('\n')
}
