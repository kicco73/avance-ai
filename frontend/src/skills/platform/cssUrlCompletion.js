const URL_PARTIAL_PATTERN = /url\(\s*(['"]?)([^'")]*)$/i

export function cssUrlCompletionSource(getAssetFiles) {
  return (context) => {
    const line = context.state.doc.lineAt(context.pos)
    const textBefore = line.text.slice(0, context.pos - line.from)
    const match = URL_PARTIAL_PATTERN.exec(textBefore)
    if (!match) return null

    const assetFiles = getAssetFiles()
    if (!assetFiles.length) return null

    const partial = match[2]
    return {
      from: context.pos - partial.length,
      options: assetFiles.map((name) => ({ label: name, type: 'file' })),
      validFor: /^[^'")]*$/
    }
  }
}
