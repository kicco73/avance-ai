// Builds the <ErrorBanner /> message/detail pair for postImportSessions'
// own per-item results ([{ file, ok, error }], file a plain label string).
// One item failing never aborts the rest. Returns null when nothing failed.
export function summarizeImportFailures(results) {
  const failures = results.filter((r) => !r.ok)
  if (!failures.length) return null

  const message = failures.length === results.length
    ? `Failed to import ${failures.length === 1 ? 'the transcript' : `all ${failures.length} transcripts`}.`
    : `Imported ${results.length - failures.length} of ${results.length} transcripts — ${failures.length} failed.`
  const detail = failures.map((r) => `${r.file}: ${r.error}`).join('\n')
  return { message, detail }
}
