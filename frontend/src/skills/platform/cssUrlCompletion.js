// CodeMirror completion source for a CSS buffer's url(...) targets —
// offers every known Theme asset's basename while the cursor sits inside
// an unclosed url( / url(' / url(" on the current line. Registered as CSS
// language data (see CodeEditor.vue), so it adds to — rather than
// replaces — the language's own default completions.
const URL_PARTIAL_PATTERN = /url\(\s*(['"]?)([^'")]*)$/i

// `getAssetFiles` is a getter, not a plain array, so each completion
// request reads whatever Theme currently holds (props are reactive; this
// extension is built once, on the editor's first load).
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
