// CodeMirror completion source for index.yml's `attachments:` lists —
// offers every known Behavior-branch attachment while the cursor sits on
// a bare list item (`- `) inside an attachments: block, or inside an
// inline `attachments: [...]` array. Registered as YAML language data
// (see CodeEditor.vue), so it adds to rather than replaces the
// language's own default completions.
const LIST_ITEM_PATTERN = /^(\s*)-\s*([^\s#]*)$/
const INLINE_ARRAY_PATTERN = /attachments:\s*\[\s*([^\]]*)$/

// Walks upward from the list item's line, looking for the nearest
// less-indented line — that's its parent key, which must be a bare
// `attachments:` for this item to belong to that list. A blank line or a
// sibling/nested line (indent >= itemIndent) doesn't end the block, so
// scanning just keeps going up past those.
function isInsideAttachmentsBlock(doc, itemLineNumber, itemIndent) {
  for (let n = itemLineNumber - 1; n >= 1; n--) {
    const text = doc.line(n).text
    if (!text.trim()) continue
    const indent = text.match(/^\s*/)[0].length
    if (indent < itemIndent) return /^\s*attachments:\s*$/.test(text)
  }
  return false
}

// `getAttachmentFiles` is a getter, not a plain array, so each completion
// request reads whatever the Behavior branch currently holds (props are
// reactive; this extension is built once, on the editor's first load).
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
