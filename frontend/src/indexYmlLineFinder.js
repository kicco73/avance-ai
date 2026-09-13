
function lineIndent(line) {
  const m = line.match(/^[ \t]*/)
  return m ? m[0].length : 0
}

function isBlankOrComment(trimmed) {
  return !trimmed || trimmed.startsWith('#')
}

function findTopLevelChildLine(lines, topKey, childKey) {
  const topPattern = new RegExp(`^${topKey}\\s*:\\s*(#.*)?$`)
  let inBlock = false
  let childIndent = null
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    const trimmed = raw.trim()
    if (!inBlock) {
      if (lineIndent(raw) === 0 && topPattern.test(trimmed)) inBlock = true
      continue
    }
    if (isBlankOrComment(trimmed)) continue
    const indent = lineIndent(raw)
    if (indent === 0) break
    if (childIndent === null) childIndent = indent
    if (indent !== childIndent) continue
    const m = trimmed.match(/^(['"]?)([^:'"]+)\1\s*:\s*(#.*)?$/)
    if (m && m[2] === childKey) return i
  }
  return null
}

export function findStateLine(lines, stateKey) {
  return findTopLevelChildLine(lines, 'states', stateKey)
}

export function findSignalLine(lines, signalName) {
  return findTopLevelChildLine(lines, 'signals', signalName)
}

export function findEnvKeyLine(lines, envKeyName) {
  return findTopLevelChildLine(lines, 'env', envKeyName)
}

export function findAttachmentLine(lines, stateKey, fileName) {
  const stateLine = findStateLine(lines, stateKey)
  if (stateLine === null) return null
  const stateIndent = lineIndent(lines[stateLine])
  let inAttachments = false
  let attachmentsIndent = null
  for (let i = stateLine + 1; i < lines.length; i++) {
    const raw = lines[i]
    const trimmed = raw.trim()
    if (isBlankOrComment(trimmed)) continue
    const indent = lineIndent(raw)
    if (indent <= stateIndent) break
    if (!inAttachments) {
      if (/^attachments\s*:\s*(#.*)?$/.test(trimmed)) {
        inAttachments = true
        attachmentsIndent = indent
      }
      continue
    }
    if (indent <= attachmentsIndent) break
    const m = trimmed.match(/^-\s*(['"]?)(.*)\1\s*$/)
    if (m && m[2] === fileName) return i
  }
  return null
}

export function findInitActionLine(lines) {
  const idx = lines.findIndex((line) => lineIndent(line) === 0 && /^init-action\s*:\s*(#.*)?$/.test(line.trim()))
  return idx === -1 ? null : idx
}

export function findActionLine(lines, stateKey, actionName) {
  const stateLine = findStateLine(lines, stateKey)
  if (stateLine === null) return null
  const stateIndent = lineIndent(lines[stateLine])
  let inActions = false
  let itemStart = null
  let itemMatches = false

  const flushItem = () => (itemStart !== null && itemMatches ? itemStart : null)

  for (let i = stateLine + 1; i <= lines.length; i++) {
    const atEnd = i === lines.length
    const raw = atEnd ? '' : lines[i]
    const trimmed = raw.trim()
    const skippable = isBlankOrComment(trimmed)
    const indent = skippable ? null : lineIndent(raw)

    const leavingState = atEnd || (!skippable && indent <= stateIndent)
    const startsNewItem = !skippable && !leavingState && trimmed.startsWith('- ')

    if (leavingState || startsNewItem) {
      const found = flushItem()
      if (found !== null) return found
      if (leavingState) return null
      itemStart = i
      itemMatches = false
    }

    if (!skippable && !leavingState) {
      if (!inActions) {
        if (/^actions\s*:\s*(#.*)?$/.test(trimmed)) inActions = true
      } else if (itemStart !== null) {
        const m = trimmed.match(/^-?\s*name\s*:\s*(['"]?)(.*?)\1\s*(#.*)?$/)
        if (m && m[2] === actionName) itemMatches = true
      }
    }
  }
  return null
}
