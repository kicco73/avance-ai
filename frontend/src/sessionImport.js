// Builds the <ErrorBanner /> message/detail pair for a batch transcript
// import's per-file results ([{ file, ok, error }]). One File failing
// never aborts the rest. Returns null when nothing failed.
export function summarizeImportFailures(results) {
  const failures = results.filter((r) => !r.ok)
  if (!failures.length) return null

  const message = failures.length === results.length
    ? `Failed to import ${failures.length === 1 ? 'the transcript' : `all ${failures.length} transcripts`}.`
    : `Imported ${results.length - failures.length} of ${results.length} transcripts — ${failures.length} failed.`
  const detail = failures.map((r) => `${r.file.name}: ${r.error}`).join('\n')
  return { message, detail }
}
