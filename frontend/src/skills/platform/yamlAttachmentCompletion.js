const LIST_ITEM_PATTERN = /^(\s*)-\s*([^\s#]*)$/
const INLINE_ARRAY_PATTERN = /attachments:\s*\[\s*([^\]]*)$/

function isInsideAttachmentsBlock(doc, itemLineNumber, itemIndent) {
  for (let n = itemLineNumber - 1; n >= 1; n--) {
    const text = doc.line(n).text
    if (!text.trim()) continue
    const indent = text.match(/^\s*/)[0].length
    if (indent < itemIndent) return /^\s*attachments:\s*$/.test(text)
  }
  return false
}

export function yamlAttachmentCompletionSource(getAttachmentFiles) {
  return (context) => {
    const line = context.state.doc.lineAt(context.pos)
    const textBefore = line.text.slice(0, context.pos - line.from)

    const inlineMatch = INLINE_ARRAY_PATTERN.exec(textBefore)
    if (inlineMatch) {
      const attachmentFiles = getAttachmentFiles()
      if (!attachmentFiles.length) return null
      const partial = inlineMatch[1].split(',').pop().trim()
      return {
        from: context.pos - partial.length,
        options: attachmentFiles.map((name) => ({ label: name, type: 'file' })),
        validFor: /^[^,\]]*$/
      }
    }

    const listMatch = LIST_ITEM_PATTERN.exec(textBefore)
    if (listMatch) {
      const [, indent, partial] = listMatch
      if (!isInsideAttachmentsBlock(context.state.doc, line.number, indent.length)) return null
      const attachmentFiles = getAttachmentFiles()
      if (!attachmentFiles.length) return null
      return {
        from: context.pos - partial.length,
        options: attachmentFiles.map((name) => ({ label: name, type: 'file' })),
        validFor: /^[^\s#]*$/
      }
    }

    return null
  }
}
