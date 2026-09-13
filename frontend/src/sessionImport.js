export function summarizeImportFailures(results) {
  const failures = results.filter((r) => !r.ok)
  if (!failures.length) return null

  const message = failures.length === results.length
    ? `Failed to import ${failures.length === 1 ? 'the transcript' : `all ${failures.length} transcripts`}.`
    : `Imported ${results.length - failures.length} of ${results.length} transcripts — ${failures.length} failed.`
  const detail = failures.map((r) => `${r.file}: ${r.error}`).join('\n')
  return { message, detail }
}
